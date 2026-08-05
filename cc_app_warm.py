"""cc_app_warm — warm process-pool mixin for the CC Translate app.

Extracted verbatim from translator.pyw's TranslatorApp. Mixed into
TranslatorApp so `self` resolves to the single assembled instance; the shared
instance state (`_warm_lock`, `_warm_pool`, `_warm_pending`, `_warm_enabled`,
`cfg`) still lives in `TranslatorApp.__init__`. This module imports only leaf
modules (never translator.pyw) so there is no import cycle.
"""

import threading

import i18n
from cc_warm import WarmClaude, WARM_POOL_DEPTH
from cc_core import CFG, direction_prompt, SYSTEM_SUFFIX, DICTIONARY_PROMPT, log_error


class WarmMixin:
    # ---------- Warm process pool ----------
    # A "profile" is a distinct (system prompt, config) combination worth
    # keeping a process warm for. We warm the two most common paths so they
    # skip the ~2s CLI cold-start: normal translation and single-word
    # dictionary lookups. Code-explain and summary stay cold (rarer, and each
    # extra warm process is a resident node process).
    WARM_PROFILES = ("translate", "dictionary")

    def _warm_key(self):
        """Config signature used to detect when the warm pool must be rebuilt
        (model or direction change). Both affect the translate prompt."""
        return (self.cfg.get(CFG.MODEL), self.cfg.get(CFG.DIRECTION))

    def _warm_system_prompt(self):
        mode = self.cfg.get(CFG.DIRECTION, "auto")
        app_language = self.cfg.get(CFG.LANGUAGE) or i18n.get_language()
        return direction_prompt(mode, app_language) + SYSTEM_SUFFIX

    def _warm_profile_spec(self, profile):
        """Return (key, system_prompt) for a warm profile, or None if unknown.
        The key is baked into the WarmClaude and re-checked at use time so a
        process warmed for one config/profile is never handed to another."""
        model = self.cfg.get(CFG.MODEL)
        if profile == "translate":
            direction = self.cfg.get(CFG.DIRECTION)
            return (("translate", model, direction), self._warm_system_prompt())
        if profile == "dictionary":
            # Direction-independent (matches _system_prompt_for's DICTIONARY_PROMPT).
            return (("dictionary", model), DICTIONARY_PROMPT)
        return None

    def _spawn_warm_async(self, profile=None):
        """Top up one profile (or every profile when profile is None) to
        WARM_POOL_DEPTH ready processes. Non-blocking (spawns run in a thread).
        In-flight spawns are counted (_warm_pending) so repeated calls — e.g.
        the post-use refill plus a concurrent stale-eviction — never over-shoot
        the target depth."""
        if not self._warm_enabled:
            return
        profiles = (profile,) if profile is not None else self.WARM_PROFILES
        # Decide how many to spawn per profile under the lock, reserving the
        # count in _warm_pending so a concurrent call sees the reservation.
        plan = []   # list of profile names to spawn (one entry per process)
        with self._warm_lock:
            generation = self._warm_generation
            for name in profiles:
                if self._warm_profile_spec(name) is None:
                    continue
                have = len(self._warm_pool.get(name, ()))
                pending = self._warm_pending.get(name, 0)
                need = WARM_POOL_DEPTH - have - pending
                for _ in range(max(0, need)):
                    plan.append(name)
                    self._warm_pending[name] = self._warm_pending.get(name, 0) + 1
        if not plan:
            return

        def _work(plan=plan, generation=generation):
            for name in plan:
                w = None
                try:
                    # Recompute the spec at spawn time so a config change while
                    # this spawn was queued produces a current-config process.
                    spec = self._warm_profile_spec(name)
                    if spec is not None:
                        key, system_prompt = spec
                        cand = WarmClaude(key[1], system_prompt, key)
                        if cand.start():
                            w = cand
                except Exception as e:
                    log_error("warm_refill", e)
                    w = None
                keep = False
                with self._warm_lock:
                    if generation == self._warm_generation:
                        self._warm_pending[name] = max(
                            0, self._warm_pending.get(name, 0) - 1)
                    if (w is not None and self._warm_enabled
                            and generation == self._warm_generation):
                        self._warm_pool.setdefault(name, []).append(w)
                        keep = True
                if w is not None and not keep:
                    try:
                        w.close()
                    except Exception:
                        pass
        threading.Thread(target=_work, daemon=True).start()

    def _take_warm(self, profile, expected_key=None):
        """Return one ready warm process for this profile, removing it from the
        pool, or None if none is ready. Evicts any stale-config processes it
        finds and triggers a refill so the pool stays topped up."""
        if not self._warm_enabled:
            return None
        spec = self._warm_profile_spec(profile)
        if spec is None:
            return None
        key = expected_key or spec[0]
        chosen = None
        discard = []
        with self._warm_lock:
            keep = []
            for w in self._warm_pool.get(profile, ()):
                if chosen is None and w.usable(key):
                    chosen = w                       # take exactly one usable
                elif expected_key is None and w.ready and w.key != key:
                    discard.append(w)                # stale config: evict
                else:
                    keep.append(w)                   # still warming — keep
            self._warm_pool[profile] = keep
        for w in discard:
            try:
                w.close()
            except Exception:
                pass
        # Refill when we took one (pool dropped) or evicted stale ones, so the
        # profile climbs back to WARM_POOL_DEPTH.
        if chosen is not None or discard:
            self._spawn_warm_async(profile)
        return chosen

    def _reset_warm_pool(self):
        """Discard every pre-warmed process and re-warm all profiles for the
        current config. Used when the model/direction changes so no process
        keeps a now-wrong system prompt."""
        with self._warm_lock:
            self._warm_generation += 1
            procs = [w for lst in self._warm_pool.values() for w in lst]
            self._warm_pool = {}
            self._warm_pending = {}
        for w in procs:
            try:
                w.close()
            except Exception:
                pass
        self._spawn_warm_async()

    def _set_warm_provider(self, provider_id):
        """Keep Claude's pool intact when selected and suspend it for others."""
        should_enable = provider_id == "claude_cli"
        if should_enable:
            self._warm_enabled = True
            return
        self._warm_enabled = False
        with self._warm_lock:
            self._warm_generation += 1
            procs = [w for lst in self._warm_pool.values() for w in lst]
            self._warm_pool = {}
            self._warm_pending = {}
        for proc in procs:
            try:
                proc.close()
            except Exception:
                pass

    def close_warm_pool(self):
        """Terminate every warm process. Called on quit."""
        self._warm_enabled = False
        with self._warm_lock:
            self._warm_generation += 1
            procs = [w for lst in self._warm_pool.values() for w in lst]
            self._warm_pool = {}
            self._warm_pending = {}
        for w in procs:
            try:
                w.close()
            except Exception:
                pass
