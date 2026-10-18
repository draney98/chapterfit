#!/bin/sh
# From Ctrl-Alt-F3: kill a stuck chaptergui and return to the desktop.
# chaptergui can grab the pointer, so the graphical session stops taking clicks.

pids=$(ps -eo pid=,cmd= | awk '
  /chaptergui|chaptertools\.gui/ && !/awk/ && !/kill-chaptergui/ { print $1 }
')

if [ -z "$pids" ]; then
  echo "no chaptergui process"
else
  echo "stopping $pids"
  kill $pids 2>/dev/null || true
  sleep 0.5
  still=$(ps -eo pid=,cmd= | awk '
    /chaptergui|chaptertools\.gui/ && !/awk/ && !/kill-chaptergui/ { print $1 }
  ')
  if [ -n "$still" ]; then
    echo "killing $still"
    kill -9 $still 2>/dev/null || true
  fi
fi

echo "switching to the desktop (vt7)"
sudo chvt 7
