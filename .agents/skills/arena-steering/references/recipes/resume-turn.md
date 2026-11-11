# V2 resume-turn recipe

If active context lacks bible text, read all required paths fully via read_file before acting, then answer steer survey. Otherwise, acknowledge the loaded bible and skip the survey.

At every turn start:
1. Run steer task for the current task.
2. On failure, run skills/arena-steering/scripts/install.sh from the checkout, then retry steer task.
3. If the second check fails, report it and stop before resuming the queue.

After the CLI check:
Name every Steering preview {repo} - Steering, where {repo} is the checkout directory name.
1. If initial steer task failed, call functions.start_process from the checkout with steer serve.
2. Keep a successful preview running and continue queued work.
