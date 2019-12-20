[![GitHub license](https://img.shields.io/github/license/RedHatInsights/marketplace-processor.svg)](https://github.com/RedHatInsights/marketplace-processor/blob/master/LICENSE)
[![Code Coverage](https://codecov.io/gh/RedHatInsights/marketplace-processor/branch/master/graph/badge.svg)](https://codecov.io/gh/RedHatInsights/marketplace-processor)
[![Updates](https://pyup.io/repos/github/RedHatInsights/marketplace-processor/shield.svg)](https://pyup.io/repos/github/RedHatInsights/marketplace-processor/)
[![Python 3](https://pyup.io/repos/github/RedHatInsights/marketplace-processor/python-3-shield.svg)](https://pyup.io/repos/github/RedHatInsights/marketplace-processor/)

# Overview
- [Getting started](#intro)
- [Development](#development)
- [Formatting Data for Marketplace Processor](#formatting_data)
- [Sending Data to Platform Ingress service for Marketplace Processor](#sending_data)
- [Advanced Topics](#advanced)

# <a name="intro"></a> Getting Started

This is a Python project developed using Python 3.6. Make sure you have at least this version installed.

# <a name="development"></a> Development

## Setup

### Obtain source for local projects
To get started developing against marketplace-processor first clone a local copy of the git repository.
```
git clone https://github.com/RedHatInsights/marketplace-processor
git clone https://github.com/RedHatInsights/insights-ingress-go
git clone https://github.com/RedHatInsights/insights-host-inventory.git
```

### Configure environment variables
This project is developed using the Django web framework. Many configuration settings can be read in from a `.env` file. An example file `.env.dev.example` is provided in the repository. To use the defaults simply run:
```
cp .env.dev.example .env
```

Modify as you see fit.

### Update /etc/hosts
The `/etc/hosts` file must be updated for Kafka and Minio.  Open your `/etc/hosts` file and add the following lines to the end:

```
127.0.0.1       kafka
127.0.0.1       minio
```

### Using pipenv
A Pipfile is provided. Pipenv is recommended for combining virtual environment (virtualenv) and dependency management (pip). To install pipenv, use pip :

```
pip3 install pipenv
```

Then project dependencies and a virtual environment can be created using:
```
pipenv install --dev
```
### Bringing up marketplace-processor with all services
First, make sure you have no zombie docker containers that could conflict with the services you are bringing up.  Run:
```
docker ps -a
```

Make sure that there are no docker containers that will conflict with the services that are about to be brought up. It is safest if you have none at all, but containers that will not conflict can be left.

To run the ingress service, marketplace-processor, and host inventory service locally, use the following command:
```
make local-dev-up
```

To check if the services are up, run:

```
docker ps --format '{{.Names}}'
```
You should see the following services up and running.
```
grafana
marketplace-processor_db-host-inventory_1
marketplace-processor_db_1
prometheus
insightsingressgo_ingress_1
insightsingressgo_kafka_1
insightsingressgo_zookeeper_1
insightsingressgo_minio_1
```

### Sending data to local marketplace-processor
To send the sample data, run the following commands:
1. Prepare the sample for sending
    ```
    make sample-data
    ```

2. Locate the temp file name.  You will see a message like the following:
    ```
    The updated report was written to temp/sample_data_ready_1561410754.tar.gz
    ```
3. Send the temp file to your local marketplace-processor.  Copy the name of this file to the upload command as shown below:
    ```
    make local-upload-data file=temp/sample_data_ready_1561410754.tar.gz
    ```
4. Watch the kafka consumer for a message to arrive.  You will see something like this in the consumer iTerm.
    ```
    {"account": "12345", "rh_account": "12345", "principal": "54321", "request_id": "52df9f748eabcfea", "payload_id": "52df9f748eabcfea", "size": 1132, "service": "qpc", "category": "tar", "b64_identity": "eyJpZGVudGl0eSI6IHsiYWNjb3VudF9udW1iZXIiOiAiMTIzNDUiLCAiaW50ZXJuYWwiOiB7Im9yZ19pZCI6ICI1NDMyMSJ9fX0=", "url": "http://minio:9000/insights-upload-perm-test/52df9f748eabcfea?AWSAccessKeyId=BQA2GEXO711FVBVXDWKM&Signature=WEgFnnKzUTsSJsQ5ouiq9HZG5pI%3D&Expires=1561586445"}
    ```

5. Look at the marketplace-processor logs to follow the report processing to completion.


## Prometheus
You can view the running Prometheus server at [http://localhost:9090](http://localhost:9090). Here, you can execute queries by typing in the name of the metric you want and pressing the `execute` button. You can also view the target that we are monitoring (our metrics endpoint) and the configuration of the Prometheus server.

If you would like to change the configuration of the Prometheus server, you can edit the configuration file found [here](https://github.com/RedHatInsights/marketplace-processor/blob/master/scripts/config/prometheus.yml). For example, if you would like to have a more accurate representation of the metrics, you can change change the scrape interval for the `marketplace` job before bringing the local development services up. Currently we are polling the `/metrics` endpoint every `10s` to mimic the scrape interval used in CI, but you can set this to `1s` for more accurate metrics in development.

## Grafana
In order to visualize the metrics that we are collecting, log in to Grafana at [http://localhost:3000](http://localhost:3000):
1. Log in using `admin` as the username and `secret` as the password.

2. Once you are logged in, click on `Create your first data source`, and select `Prometheus`. Leave all of the defaults, but enter `http://docker.for.mac.localhost:9090` into the `URL` field. Scroll down and click `Save & Test`.

3. Now you can import our development dashboard. Click on the `+` in the lefthand toolbar and select `Import`. Next, select `Upload .json file` in the upper right-hand corner. Now, import [dev-grafana.json](https://github.com/RedHatInsights/marketplace-processor/blob/master/grafana/dev-grafana.json). Finally, click `Import` to begin using the yupana dashboard to visualize the data.


### Bringing down marketplace-processor and all services
To bring down all services run:
```
make local-dev-down
```

## Testing and Linting

Marketplace processor uses tox to standardize the environment used when running tests. Essentially, tox manages its own virtual environment and a copy of required dependencies to run tests. To ensure a clean tox environment run:
```
tox -r
```

This will rebuild the tox virtual env and then run all tests.

To run unit tests specifically:
```
tox -e py36
```

If you would like to run a single test you can do this.
```
tox -e py36 -- processor.tests_report_processor.ReportProcessorTests.test_archiving_report
```
**Note:** You can specify any module or class to run all tests in the class or module.

To lint the code base:
```
tox -e lint
```

To check whether or not the product manifest needs to be updated, run the following:
```
make check-manifest
```

If the manifest is out of date, you can run the following to update it:
```
make manifest
```


# <a name="formatting_data"></a> Formatting Data for Marketplace Processor
Below is a description of how to create data formatted for the marketplace-processor service.

## Marketplace Processor tar.gz File Format Overview
Marketplace Processor retrieves data from the Platform Ingress service.  Marketplace Processor requires a specially formatted tar.gz file.  Files that do not conform to the required format will be marked as invalid and no processing will occur.  The tar.gz file must contain a metadata JSON file and one or more report slice JSON files. The file that contains metadata information is named `metadata.json`, while the files containing host data are named with their uniquely generated UUID4 `report_slice_id` followed by the .json extension. You can download [sample.tar.gz](https://github.com/RedHatInsights/marketplace-processor/raw/master/sample.tar.gz) to view an example.

## Marketplace Processor Meta-data JSON Format
Metadata should include information about the sender of the data and the report slices included in the tar.gz file. Below is a sample metadata section for a report with 2 slices:
```
{
    "report_id": "05f373dd-e20e-4866-b2a4-9b523acfeb6d",
    "source": "f75a249f-1a46-450e-8af1-c3bbb2e75edc",  # Cluster ID
    "source_metadata": {
        "any_cluster_info_you_want": "some stuff that will not be validated but will be logged"
    },
    "report_slices": {
        "2dd60c11-ee5b-4ddc-8b75-d8d34de86a34": {
            "number_metrics": 1
        },
        "eb45725b-165a-44d9-ad28-c531e3a1d9ac": null
    }
}
```


## Marketplace Processor Report Slice JSON Format
Report slices are a slice format is TBD.


# <a name="sending_data"></a> Sending Data to Platform Ingress service for Marketplace Processor
Data being uploaded to the Platform must be in `tar.gz` format containing the `.json` files with the given JSON structure above. It is important to note that Marketplace Processor processes & tracks reports based on their UUIDS, which means that data with a specific UUID cannot be uploaded more than once, or else the second upload will be archived and not processed. Therefore, before every upload we need to generate a new UUID and replace the current one with it if we want to upload the same data more than once. Use the following instructions to prepare and upload a sample or custom report.

## Uploading Data
After preparing the data with new UUIDs through either of the above steps, you can upload it to Insights.  Additionally, you must export the following required information as environment variables or add them to your `.env` file.  See `.env.external.example`.
```
INGRESS_URL=<ingress-url>
RH_USERNAME=<your-username>
RH_PASSWORD=<your-password>
```

To upload the data, run:
```
make upload-data file=<path/to/your-data.tar.gz>
```

You need to replace `<path/to/your-data.tar.gz>` with either the absolute or relative path to the `tar.gz` file that you want to upload to Insights.

After running this command if you see `HTTP 202` like the following lines in your output logs, it means your file upload to Insights was successful:
```
* Connection state changed (MAX_CONCURRENT_STREAMS updated)!
< HTTP/2 202
```

# <a name="advanced"></a> Advanced Topics
## Database

PostgreSQL is used as the database backend for Marketplace Processor. If modifications were made to the .env file the docker-compose file will need to be modified to ensure matching database credentials. Several commands are available for interacting with the database.

Assuming the default .env file values are used, to access the database directly using psql run:
```
psql postgres -U postgres -h localhost -p 15432
```

## Run Server with gunicorn

To run a local gunicorn server with marketplace-processor do the following:
```
make server-init
gunicorn config.wsgi -c ./marketplace/config/gunicorn.py --chdir=./marketplace/
```
