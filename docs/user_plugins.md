# User plugins

You can add your own plugins without modifying the fingertip package
by placing Python files in a user plugin directory.
User plugins are resolved before built-in ones,
so they can also serve as overrides.


## Plugin directory

The default location is `~/.config/fingertip/plugins/`.

Override it by setting the `FINGERTIP_USER_PLUGINS` environment variable:

``` bash
$ export FINGERTIP_USER_PLUGINS=/path/to/my/plugins
```


## Creating a plugin

A plugin is a `.py` file, same as built-in plugins.
A plugin used as a step can define a `main` callable,
but it can also provide other functions that are accessible
with the dot notation (`plugin.function`).

The file name maps to the plugin name:

* `runtest` -> `~/.config/fingertip/plugins/runtest.py`
* `foo.bar` -> either `~/.config/fingertip/plugins/foo/bar.py` (with `main`),
  or function `bar` in `~/.config/fingertip/plugins/foo.py`


## Example: creating an alias

To make `fingertip fedora` resolve to the `os.fedora` plugin:

``` bash
$ mkdir -p ~/.config/fingertip/plugins
$ echo from fingertip.plugins.os.fedora import main \
  > ~/.config/fingertip/plugins/fedora.py
```

Now `fingertip fedora` works the same as `fingertip os.fedora`.

If the user plugin file is not found, fingertip falls through
to the regular built-in plugin lookup, so existing plugins
continue to work normally.
