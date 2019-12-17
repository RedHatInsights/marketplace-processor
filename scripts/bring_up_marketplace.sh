#!/bin/bash
set -x

cd ..

. .env
docker-compose up -d
./scripts/countdown.sh 'Waiting for marketplace db to be ready.' 15 'marketplace db is ready'
pipenv run make server-init
pipenv run make serve
