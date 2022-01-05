#!/bin/zsh

PAYLOAD_COUNT=$1
SAMPLE_DIR=./temp

repeat $PAYLOAD_COUNT do sleep 1; make sample-data; done

ls $SAMPLE_DIR

for entry in "$SAMPLE_DIR"/*
do
  echo "$entry"
  make upload-proxy-data file="$entry"
done

for entry in "$SAMPLE_DIR"/*
do
  rm "$entry"
done
