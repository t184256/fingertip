#!/usr/bin/env bash
set -uex

FINGERTIP='/usr/bin/python3 .'
BASES=('backend.podman-criu centos' 'os.fedora' 'os.alpine')
TESTS=(
	self_test.greeting
	self_test.prompts
	self_test.subshell
	self_test.wait_for_it
	'ssh true'
)

for BASE in "${BASES[@]}"; do
	for TEST in "${TESTS[@]}"; do
		[[ $TEST = self_test.greetings && $BASE = os.fedora ]] \
			&& continue  # takes too long
		[[ $TEST = 'ssh true' && $BASE =~ backend.podman-criu* ]] \
			&& continue  # doesn't have ssh

		$FINGERTIP $BASE + $TEST
	done
done
