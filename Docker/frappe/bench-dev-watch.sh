#!/bin/bash
trap "kill -- -$$" EXIT
bench watch
