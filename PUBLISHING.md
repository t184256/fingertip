# Release versioning

fingertip uses
[semantic versioning 2.0.0](https://semver.org/spec/v2.0.0.html).


## Publishing to Pypi

To publish a new version to pypi, install Poetry (`>= 1.0`) and
[poetry-dynamic-versioning](https://pypi.org/project/poetry-dynamic-versioning).
Test it with

``` bash
$ poetry build
```

Publish with

``` bash
$ poetry publish
```


## Publishing to COPR

Just push, and
https://copr.fedorainfracloud.org/coprs/asosedkin/fingertip
should pick up and build all versions from master, tagged or not.
