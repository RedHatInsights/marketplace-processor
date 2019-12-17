[![GitHub license](https://img.shields.io/github/license/RedHatInsights/marketplace-processor.svg)](https://github.com/RedHatInsights/marketplace-processor/blob/master/LICENSE)
[![Code Coverage](https://codecov.io/gh/RedHatInsights/marketplace-processor/branch/master/graph/badge.svg)](https://codecov.io/gh/RedHatInsights/marketplace-processor)
[![Documentation Status](https://readthedocs.org/projects/marketplace-processor/badge/)](https://marketplace-processor.readthedocs.io/en/latest/)
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
marketplace-processor_db-host-inventory_1
marketplace-processor_db_1
insightsingressgo_ingress_1
insightsingressgo_kafka_1
insightsingressgo_zookeeper_1
insightsingressgo_minio_1
```


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
Metadata should include information about the sender of the data, Host Inventory API version, and the report slices included in the tar.gz file. Below is a sample metadata section for a report with 2 slices:
```
{
    "report_id": "05f373dd-e20e-4866-b2a4-9b523acfeb6d",
    "source": "Marketplace Processor",
    "source_metadata": {
        "any_cluster_info_you_want": "some stuff that will not be validated but will be logged"
    },
    "report_slices": {
        "2dd60c11-ee5b-4ddc-8b75-d8d34de86a34": {
            "number_metrics": 1
        },
        "eb45725b-165a-44d9-ad28-c531e3a1d9ac": {
            "number_metrics": 1
        }
    }
}
```

An API specification of the metadata can be found in [metadata.yml](https://github.com/RedHatInsights/marketplace-processor/blob/master/docs/metadata.yml).

## Marketplace Processor Report Slice JSON Format
Report slices are a slice format is TBD.


# <a name="sending_data"></a> Sending Data to Platform Ingress service for Marketplace Processor
Data being uploaded to the Platform must be in `tar.gz` format containing the `.json` files with the given JSON structure above. It is important to note that Marketplace Processor processes & tracks reports based on their UUIDS, which means that data with a specific UUID cannot be uploaded more than once, or else the second upload will be archived and not processed. Therefore, before every upload we need to generate a new UUID and replace the current one with it if we want to upload the same data more than once. Use the following instructions to prepare and upload a sample or custom report.

## Uploading Data
After preparing the data with new UUIDs through either of the above steps, you can upload it to Insights.  Additionally, you must export the following required information as environment variables or add them to your `.env` file.  See `.env.external.example`.
```
RH_ACCOUNT_NUMBER=<your-account-number>
RH_ORG_ID=<your-org-id>
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
gunicorn config.wsgi -c ./marketplace-processor/config/gunicorn.py --chdir=./marketplace-processor/
```
