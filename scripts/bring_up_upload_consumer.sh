#!/bin/bash
set -x

cd ../../insights-ingress-go/development/
. .env
docker-compose -f local-dev-start.yml exec kafka kafka-console-consumer --topic=platform.upload.mkt --bootstrap-server=localhost:29092
