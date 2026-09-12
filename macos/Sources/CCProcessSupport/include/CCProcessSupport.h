#ifndef CC_PROCESS_SUPPORT_H
#define CC_PROCESS_SUPPORT_H

#include <sys/types.h>

int cc_spawn_cli_version(const char *executable, const char *home, const char *path,
                         int output, int errors, pid_t *pid);
int cc_cli_has_exited(pid_t pid, int *exited);
int cc_cli_signal_group(pid_t pid, int signal);
int cc_cli_reap(pid_t pid, int *exit_code);

#endif
