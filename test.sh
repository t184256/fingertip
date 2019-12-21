#!/usr/bin/env bash
set -uex

FINGERTIP='/usr/bin/python3 .'
BASES=('backend.podman-criu centos' 'os.fedora')
TESTS=('greeting' 'prompts' 'subshell' 'wait_for_it')

for BASE in "${BASES[@]}"; do
	for TEST in "${TESTS[@]}"; do
		$FINGERTIP $BASE + self_test.$TEST
	done
done
