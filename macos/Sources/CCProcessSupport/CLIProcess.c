#include "CCProcessSupport.h"
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/proc.h>
#include <sys/sysctl.h>
#include <sys/wait.h>
#include <unistd.h>

int cc_process_support_abi(void) { return 1; }

int cc_spawn_cli_version(const char *executable, const char *home, const char *path,
                         int output, int errors, pid_t *pid) {
    if (!executable || !home || !path || !pid || output < 3 || errors < 3 ||
        output == errors || executable[0] != '/' || home[0] != '/') return EINVAL;

    char *home_env = NULL;
    char *path_env = NULL;
    if (asprintf(&home_env, "HOME=%s", home) < 0) return ENOMEM;
    if (asprintf(&path_env, "PATH=%s", path) < 0) {
        free(home_env);
        return ENOMEM;
    }
    char *argv[] = {(char *)executable, "--version", NULL};
    char *env[] = {home_env, path_env, "LANG=en_US.UTF-8", NULL};
    posix_spawnattr_t attr;
    posix_spawn_file_actions_t actions;
    int error = posix_spawnattr_init(&attr);
    if (error) goto free_environment;
    error = posix_spawn_file_actions_init(&actions);
    if (error) goto destroy_attributes;

    sigset_t empty, defaults;
    sigemptyset(&empty);
    sigemptyset(&defaults);
    sigaddset(&defaults, SIGTERM);
    sigaddset(&defaults, SIGINT);
    sigaddset(&defaults, SIGPIPE);
    error = posix_spawnattr_setsigmask(&attr, &empty);
    if (!error) error = posix_spawnattr_setsigdefault(&attr, &defaults);
    // A new group is created before exec; no parent-side setpgid race.
    if (!error) error = posix_spawnattr_setpgroup(&attr, 0);
    if (!error) error = posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETPGROUP |
        POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF | POSIX_SPAWN_CLOEXEC_DEFAULT);
    if (!error) error = posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0);
    if (!error) error = posix_spawn_file_actions_adddup2(&actions, output, STDOUT_FILENO);
    if (!error) error = posix_spawn_file_actions_adddup2(&actions, errors, STDERR_FILENO);
    if (!error) error = posix_spawn_file_actions_addclose(&actions, output);
    if (!error) error = posix_spawn_file_actions_addclose(&actions, errors);
    if (!error) error = posix_spawn_file_actions_addchdir_np(&actions, home);
    if (!error) error = posix_spawn(pid, executable, &actions, &attr, argv, env);

    posix_spawn_file_actions_destroy(&actions);
destroy_attributes:
    posix_spawnattr_destroy(&attr);
free_environment:
    free(home_env);
    free(path_env);
    return error;
}

int cc_cli_has_exited(pid_t pid, int *exited) {
    if (pid <= 1 || !exited) return EINVAL;
    siginfo_t info = {0};
    int result;
    // Hold the leader (including as a zombie) until all group signals are sent.
    // Reaping first would allow a recycled PID/PGID to target another process.
    do {
        result = waitid(P_PID, (id_t)pid, &info, WEXITED | WNOWAIT | WNOHANG);
    } while (result == -1 && errno == EINTR);
    if (result == -1) return errno;
    *exited = info.si_pid == pid;
    return 0;
}

int cc_cli_reap(pid_t pid, int *exit_code) {
    if (pid <= 1 || !exit_code) return EINVAL;
    int status;
    pid_t result;
    do {
        result = waitpid(pid, &status, WNOHANG);
    } while (result == -1 && errno == EINTR);
    if (result == -1) return errno;
    if (result == 0) return EAGAIN;
    *exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    return 0;
}

int cc_cli_signal_group(pid_t pid, int signal) {
    if (pid <= 1 || (signal != SIGTERM && signal != SIGKILL)) return EINVAL;
    int exited = 0;
    int error = cc_cli_has_exited(pid, &exited);
    if (error) return error;
    if (kill(-pid, signal) == 0 || errno == ESRCH) return 0;
    error = errno;
    if (error != EPERM) return error;

    // Darwin returns EPERM even for a group containing only zombies. Do not
    // swallow a real permission failure: inspect only this pinned group.
    error = cc_cli_has_exited(pid, &exited);
    if (error) return error;
    if (!exited) return EPERM;
    int mib[] = {CTL_KERN, KERN_PROC, KERN_PROC_PGRP, pid};
    for (int attempt = 0; attempt < 3; ++attempt) {
        size_t size = 0;
        if (sysctl(mib, 4, NULL, &size, NULL, 0) == -1) return errno;
        if (size == 0) return 0;
        if (size > 1024 * 1024) return EOVERFLOW;
        struct kinfo_proc *members = malloc(size);
        if (!members) return ENOMEM;
        if (sysctl(mib, 4, members, &size, NULL, 0) == -1) {
            error = errno;
            free(members);
            if (error == ENOMEM) continue;
            return error;
        }
        error = size % sizeof(*members) == 0 ? 0 : EINVAL;
        for (size_t i = 0; !error && i < size / sizeof(*members); ++i) {
            if (members[i].kp_proc.p_stat != SZOMB) error = EPERM;
        }
        free(members);
        return error;
    }
    return EAGAIN;
}
