There are three types of plugins, differentiated by their outputs.


# Regular plugins

Regular plugins take machines and return machines:

``` python
def regular_plugin(m, arg, kwarg='default value'):
    assert hasattr(m, 'required_feature')
    if 'deemed_necessary':
        m = m.apply(extra_step, ...)  # is cached / reused

    with m:
        do_something_to(m)
    return m
```

If a plugin should support being the first in the pipeline,
you'll have to use this and stick to kwargs:

``` python
def possibly_first_regular_plugin(m=None, arg=None, kwarg='default value'):
    m = m or fingertip.build('reasonable_starting_machine')
    ...
    with m:
        do_something_to(m)
    return m
```

Regular plugin are there to build machines.
`.apply`'ing ones modifies machines.

Building the same step twice at the same time will result in
one step waiting for the other one to complete and then reusing the result.


# Transient plugins

Sometimes the plugin's output doesn't matter at all.
An example could be a check that a machine satisfies some property,
that aborts it doesn't and does absolutely nothing useful otherwise.
In this case it'd be a waste of time to save the machine properly,
as the next step should just build up on the parent image instead.
This way, whatever happens in a transient plugin is effectively cancelled,
even the logs won't reflect that it has ever happened.

While any plugin can return None out of the blue to the same effect,
properly optimized transient plugins look like this:


``` python
@fingertip.transient
def always_transient_plugin(m):
    with m:
        do_something_meaningless_or_destructive_to(m)
```

`@fingertip.transient` is just a hint to `.apply()`
and it doesn't modify the behavior of the function in any way
if it's invoked directly or inside a machines's `with`-block.
Don't expect something to revert the changes in these cases,
there's simply no 'something' to do it.

Sometimes the decision to go transient might depend on the args:


``` python
def _should_it_be_transient(m, cache=False):
    return not cache or some_decision_based_on_inspecting(m)

@fingertip.transient(when=_should_it_be_transient)
def sometimes_transient_plugin(m, cache=False):
    with m:
        do_something_meaningless_or_destructive_to(m)
```


If even this is enough, please reconsider the interface of your plugin.
A plugin can return `None` at any time,
but that's unpredictable and inefficient in comparison to cases
where fingertip knows it in advance.
Best attempt at optimizing such a strange plugin would look like this
(won't allow running several instances with same args at once though):

``` python
def undecisively_transient_plugin(m):
    decided_to_be_transient = weather_on_Mars() == 'nice'
    with m.transient() if decided_to_be_transient else m as m:
        ...
    if not decided_to_be_transient:
        return m
```

Transient plugins are there to either implement tests and assertions
or perform side effects while keeping the original machine intact.
Or for actions unrelated to machine-building altogether, e.g., `cleanup` plugin.

`apply`ing them returns machines in pre-plugin-execution state,
provided the plugin has executed succesfully.

Several transient plugins can be executed with the same args
without locking on each other.

Execution in transient plugins can be subject to otherwise unavailable
optimizations, e.g., keeping a VM image entirely in RAM
(`backend.qemu` utilizes this).


# Transient-when-last

This one is the weirdest breed, designed to accommodate a specific user demand.
Sometimes the plugins' log output or other side-effects are valuable,
and the modifications to the machine might be useful for the latter steps,
but, if there aren't any,
gracefully spinning down and saving the resulting machine is wasteful
in terms of either time or disk space.

A good example is `script.run`.
When used for debugging a script that is constantly changed,
persisting the resulting images would just kill your SSD sooner.
Making the plugin transient, on the other hand, would revert the changes
and, e.g., prevent the follow-up step of `fingertip ... + script.run + ssh`
from inspecting what went wrong.

This way, `script.run` is better off with:

``` python
@fingertip.transient(when='last')
def run(m, scriptpath):
    with m:
        m.expiration.depend_on_a_file(scriptpath)
        m.upload(scriptpath, '/script')
        m('chmod +x /script && /script')
    return m
```

This way it'll be both efficient when used as the last step,
and the changes will get preserved if there are any follow-up steps.

Note that this way the result of the script execution
won't be cached if it's the last step,
and might be cached for way too long if it's not.
The actual `script.run` uses a more complicated trick:

``` python
def _should_run_be_transient(m, scriptpath, cache=0, **unused_kwargs):
    return False if cache else 'last'

@fingertip.transient(when=_should_run_be_transient)
def run(m, scriptpath, cache=0, ...):
    with m:
        if cache is not True:
            m.expiration.cap(cache)
        m.expiration.depend_on_a_file(scriptpath)
        m.upload(scriptpath, '/script')
        m('chmod +x /script && /script')
    return m
```

This way the machine will have an expiration cap of zero by default,
enabling the next steps to reuse it,
but preventing it from being reused by any other `fingertip` invocation.

Now, there's a plenty of ways to invoke it with different caching behavior:

* `fingertip ... + script.X script`  ---  no cache, never persist, always rerun
* `fingertip ... + script.X script + ssh`  ---  cache just for this invocation
* `fingertip ... + script.X script --cache=1h`  ---  cache for 1h at most
* `fingertip ... + script.X script --cache=1h + ssh`  ---  cache or reuse if fresh
* `fingertip ... + transient script.X script + ssh`  ---  revert

Locking and performance optimizations
will depend on whether the plugin is the last one or not.
If you're using Python and not fingertip's command-line intefaces
you'll have to pass `fingertip_last_step=True` to `.apply()`
to enable the optimizations, or `when='last'` won't have any effect.
Doing so will return not a machine, but a path to an execution log!

Transient-when-last plugins are for
not caching steps if there are no follow-up steps to consume the results.
