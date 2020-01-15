with import <nixpkgs> {};

let
  fsmonitor = python36Packages.buildPythonPackage {
    pname = "fsmonitor";
    version = "0.1p";
    src = fetchFromGitHub {
      owner = "shaurz";
      repo = "fsmonitor";
      rev = "4d84d9817dce7b274cb4586b5c2091dea96982f9";
      sha256 = "1cp7par6pvm0d2m40wylm445l5yy7zngjdd8bc34xy5vwhvcmb27";
    };
    doCheck = false;

    meta = with lib; {
      homepage = http://github.com/shaurz/fsmonitor;
      description = "Filesystem monitoring library for Python";
      license = licenses.mit;
    };
  };
in
(python36.withPackages (ps: with ps; [
  cachecontrol
  cloudpickle
  coloredlogs
  fasteners
  #fsmonitor
  paramiko
  pexpect
  pyxdg
  requests
  requests-mock
])).env
