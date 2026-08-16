#!/bin/bash

# Script that installs build-package.sh to compile glibc packages.
# Uses the vheditor termux-packages fork so the build scripts and patch-repo.sh
# (com.termux -> vn.vhn.vsc rebrand) match the rest of the vheditor toolchain.

BRANCH="master"

git clone --depth 1 -b ${BRANCH} --single-branch https://github.com/vhqtvn/termux-packages.git

for i in build-package.sh clean.sh packages x11-packages root-packages scripts ndk-patches patch-repo.sh; do
	rm -fr ./${i}
	cp -r ./termux-packages/${i} ./
done

rm -fr termux-packages
