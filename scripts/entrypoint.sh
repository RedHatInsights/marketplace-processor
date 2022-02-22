#!/bin/bash

set -e
cd $APP_HOME

python ./manage.py migrate --noinput

python ./manage.py runserver  --noreload 0.0.0.0:8000
