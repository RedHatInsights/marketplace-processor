#!/bin/bash
set -x

upload_dir_path='../../insights-ingress-go/development/'
cp -f config/.upload_env $upload_dir_path/.env
cd $upload_dir_path
. .env
docker-compose -f local-dev-start.yml up --build
