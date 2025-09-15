# click-async-plugins

This is a proof-of-concept of a Python [asyncio](https://docs.python.org/3/library/asyncio.html) plugin architecture based on
[click](https://click.palletsprojects.com/).

Instead of writing [functions that make up sub-commands](https://click.palletsprojects.com/en/stable/commands-and-groups/#basic-group-example), you write asynchronous "lifespan functions" (`AsyncGenerator`), like this one:

```Python
@cli_core.plugin_command
@click.option("--times", type=int)
async def myplugin(times: int) -> PluginLifespan:

    # code to set things up goes here

    async def long_running_task(*, sleep: float = 1):
        # task initialisation can happen here

        try:
            while True:
                await asyncio.sleep(sleep)

        finally:
            # code to clean up the task goes here
            pass

    yield long_running_task(sleep=3600)

    # code to tear things down goes here
```

Multiple such plugins can be defined/added to `core` (see the [demo code](https://github.com/madduck/click-async-plugins/blob/main/demo.py)). These plugins will all have their setup code called in turn. If, after setup, a plugin yields a coroutine (e.g. a long-running task), this task is scheduled with the main event loop, but this is optional, and tasks that yield nothing (`None`) will just sleep until program termination. Upon termination, the plugins' teardown code is invoked (in reverse order).

Here's what the demo code logs to the console. Two plugins are invoked. The first counts down from 3 each second, and notifies subscribers of each number. The second plugin — "echo" — just listens for updates from the "countdown" task and echoes them.

```raw
$ python demo.py countdown --from 3 echo --immediately
DEBUG:root:Setting up task for 'echo'
DEBUG:root:Setting up task for 'countdown'
DEBUG:root:Scheduling task for 'echo'
DEBUG:root:Waiting for update to 'countdown'…
DEBUG:root:Scheduling task for 'countdown'
INFO:root:Counting down… 3
DEBUG:root:Notifying subscribers of update to 'countdown'…
INFO:root:Countdown currently at 3
DEBUG:root:Waiting for update to 'countdown'…
INFO:root:Counting down… 2
DEBUG:root:Notifying subscribers of update to 'countdown'…
INFO:root:Countdown currently at 2
DEBUG:root:Waiting for update to 'countdown'…
INFO:root:Counting down… 1
DEBUG:root:Notifying subscribers of update to 'countdown'…
INFO:root:Countdown currently at 1
DEBUG:root:Waiting for update to 'countdown'…
INFO:root:Finished counting down
^C
DEBUG:root:Task for 'echo' cancelled
DEBUG:root:Terminating…
DEBUG:root:Lifespan over for countdown
DEBUG:root:Lifespan over for echo
DEBUG:root:Finished.
```

I hope you get the idea.

Looking forward to your feedback.

Oh, and if someone wanted to turn this into a proper package with tests and everything, I think it could be published to pip/pypy. I need to stop shaving this yak now, though.

© 2025 martin f. krafft <<click-async-plugins@pobox.madduck.net>>
