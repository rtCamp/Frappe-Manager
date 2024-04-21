#!/bin/bash
cleanup (){
    kill -s SIGTERM -- -$$
}
# Trap SIGQUIT, SIGTERM, SIGINT
trap cleanup SIGQUIT SIGTERM SIGINT
bench watch
