#!/bin/bash
cleanup (){
    kill -s SIGTERM -- -$$
}
trap cleanup SIGQUIT SIGTERM
bench watch
