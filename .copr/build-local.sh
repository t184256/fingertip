#!/bin/bash

# called internally by fingertip os.fedora + software.fingertip

set -uexo pipefail

version=9.9.9
release=999
tarball=fingertip.tar
outdir=/tmp/fingertip/srpms
spec=rpm/fingertip.template.spec

mkdir -p ~/rpmbuild/SOURCES

cp $spec fingertip.spec
sed -i "s|{{ver}}|$version|g" fingertip.spec
sed -i "s|{{rel}}|$release|g" fingertip.spec
sed -i "s|^Source0:.*|Source0:$tarball|g" fingertip.spec
sed -i "s|fingertip-{{git_commit}}|fingertip|g" fingertip.spec
git log "--format=* %cd %aN <%ae> - %h %n- %s%n" "--date=format:%a %b %d %Y" >> fingertip.spec

tar --transform "s|^|fingertip/|" -cf ~/rpmbuild/SOURCES/$tarball .

mkdir -p $outdir
rpmbuild -bs fingertip.spec \
    --define "_srcrpmdir $outdir" \
    --define "_sourcedir $HOME/rpmbuild/SOURCES"

rm -f fingertip.spec
