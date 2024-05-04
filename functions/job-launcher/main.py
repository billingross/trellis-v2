import os
import pdb
import sys
import json
import time
import uuid
import yaml
import base64
import random
import hashlib

import trellisdata as trellis

from google.cloud import storage
from google.cloud import pubsub

from datetime import datetime
from dsub.commands import dsub

from envyaml import EnvYAML

ENVIRONMENT = os.environ.get('ENVIRONMENT')
if ENVIRONMENT == 'google-cloud':
    # Set up the Google Cloud Logging python client library
    # source: https://cloud.google.com/blog/products/devops-sre/google-cloud-logging-python-client-library-v3-0-0-release
    import google.cloud.logging
    client = google.cloud.logging.Client()
    # log_level=10 is equivalent to DEBUG; default is 20 == INFO
    # Gcloud Python logging client: https://googleapis.dev/python/logging/latest/client.html?highlight=setup_logging#google.cloud.logging_v2.client.Client.setup_logging
    # Logging levels: https://docs.python.org/3/library/logging.html#logging-levels
    client.setup_logging(log_level=10)

    # use Python's standard logging library to send logs to GCP
    import logging

    FUNCTION_NAME = os.environ['K_SERVICE']
    PROJECT_ID = os.environ['PROJECT_ID']
    ENABLE_JOB_LAUNCH = os.environ['ENABLE_JOB_LAUNCH']

    # Load Trellis configuration
    config_doc = storage.Client() \
                .get_bucket(os.environ['CREDENTIALS_BUCKET']) \
                .get_blob(os.environ['CREDENTIALS_BLOB']) \
                .download_as_string()
    TRELLIS_CONFIG = yaml.safe_load(config_doc)

    # Load launcher configuration
    #launcher_document = storage.Client() \
    #                    .get_bucket(os.environ['CREDENTIALS_BUCKET']) \
    #                    .get_blob(TRELLIS_CONFIG["JOB_LAUNCHER_CONFIG"]) \
    #                    .download_as_string()
    with open("job-launcher.yaml", 'r') as file_handle:
        launcher_document = file_handle.read()
    task_generator = yaml.load_all(launcher_document, Loader=yaml.FullLoader)
    TASKS = {}
    for task in task_generator:
        TASKS[task.name] = task

    PUBLISHER = pubsub.PublisherClient()

def launch_dsub_task(dsub_args):
    try:
        result = dsub.dsub_main('dsub', dsub_args)
    except ValueError as exception:
        logging.error(f'Problem with dsub arguments: {dsub_args}')
        raise
    except:
        print("> Unexpected error:", sys.exc_info())
        raise
        #for arg in dsub_args:
        #    print(arg)
        #return(sys.exc_info())
    return(result)

def create_dsub_job_args_OLD(job_dict):
    """ Convert the job description dictionary into a list
        of dsub supported arguments.

    Args:
        neo4j_job_dict (dict): Event payload.
    Returns:
        list: List of "--arg", "value" pairs which will
            be passed to dsub.
    """

    dsub_args = [
        "--name", job_dict["dsubName"],
        "--label", f"trellis-id={job_dict['trellisTaskId']}",
        "--label", f"trellis-name={job_dict['name']}",
        "--label", f"wdl-call-alias={job_dict['name']}",
        "--provider", job_dict["provider"], 
        "--user", job_dict["user"], 
        "--regions", job_dict["regions"],
        "--project", job_dict["project"],
        "--min-cores", str(job_dict["minCores"]), 
        "--min-ram", str(job_dict["minRam"]),
        "--boot-disk-size", str(job_dict["bootDiskSize"]), 
        "--image", job_dict["image"], 
        "--logging", job_dict["logging"],
        "--disk-size", str(job_dict["diskSize"]),
        "--command", job_dict["command"],
        "--use-private-address",
        #"--network", job_dict["network"],
        #"--subnetwork", job_dict["subnetwork"],
        "--enable-stackdriver-monitoring",
    ]

    # Argument lists
    for key, value in job_dict["inputs"].items():
        dsub_args.extend([
                          "--input", 
                          f"{key}={value}"])
    for key, value in job_dict['envs'].items():
        dsub_args.extend([
                          "--env",
                          f"{key}={value}"])
    for key, value in job_dict['outputs'].items():
        dsub_args.extend([
                          "--output",
                          f"{key}={value}"])
    for key, value in job_dict['dsubLabels'].items():
        dsub_args.extend([
                          "--label",
                          f"{key}={value}"])

    return dsub_args

def create_dsub_job_args(task_configuration):
    """ Convert the job description dictionary into a list
        of dsub supported arguments.

    Args:
        neo4j_job_dict (dict): Event payload.
    Returns:
        list: List of "--arg", "value" pairs which will
            be passed to dsub.
    """

    dsub_args = [
        "--name", task_configuration['name'],
        "--label", f"job-request-id={task_configuration['job_request_id']}",

        "--project", task_configuration["project"],
        "--min-cores", task_configuration['virtual_machine.minCores'], 
        "--min-ram", task_configuration['virtual_machine.minRam'],
        "--boot-disk-size", task_configuration['virtual_machine.bootDiskSize'],
        "--disk-size", task_configuration["virtual_machine.diskSize"],
        "--image", task_configuration["virtual_machine.image"],
        "--provider", task_configuration["dsub.provider"],
        "--regions", task_configuration["dsub.regions"],
        "--user", task_configuration["dsub.user"], 
        "--logging", task_configuration["dsub.logging"],
        "--script", task_configuration["dsub.script"],
        "--use-private-address",
        "--enable-stackdriver-monitoring",
    ]

    if task_configuration.get('network'):
        dsub_args['--network'] = task_configuration['network']
    if task_configuration.get('subnetwork'):
        dsub_args['--subnetwork'] = task_configuration['subnetwork']

    # Argument lists
    for key, value in task_configuration["dsub.inputs"].items():
        dsub_args.extend([
                          "--input", 
                          f"{key}={value}"])
    for key, value in job_dict['dsub.environment_variables'].items():
        dsub_args.extend([
                          "--env",
                          f"{key}={value}"])
    for key, value in job_dict['dsub.outputs'].items():
        dsub_args.extend([
                          "--output",
                          f"{key}={value}"])
    for key, value in job_dict['dsub.labels'].items():
        dsub_args.extend([
                          "--label",
                          f"{key}={value}"])

    return dsub_args

def launch_job(event, context):
    """When an object node is added to the database, launch any
       jobs corresponding to that node label.

       Args:
            event (dict): Event payload.
            context (google.cloud.functions.Context): Metadata for the event.
    """
    
    query_response = trellis.QueryResponseReader(
                        context = context,
                        event = event)

    logging.info(f"> job-launcher: Received message (context): {query_response.context}.")
    logging.info(f"> job-launcher: Message header: {query_response.header}.")
    logging.info(f"> job-launcher: Message body: {query_response.body}.")

    # TODO: Get and validate the JobRequest node
    # Check that there is only one node
    # Check that it is a JobRequest node
    if len(query_response.nodes) > 1:
        raise ValueError("> job-launcher: Expected a single JobRequest node, but got multiple nodes.")
    
    job_request_node = query_response.nodes[0]
    if not 'JobRequest' in job_request_node['labels']:
        raise ValueError("> job-launcher: Expected node to have label 'JobRequest', but it does not: {job_request_node['labels']}.")

    # Set the JobRequest node properties to be environment variables
    # so that when the function loads the task configuration file,
    # it automatically populates the variable values with those
    # from the the JobRequest node that are now stored as environment
    # variables.
    logging.debug(f"> job-launcher: JobRequest node properties = {job_request_node['properties']}")
    for key, value in job_request_node['properties'].items():
        os.environ[key] = value

    # TODO: Try using EnvYAML to load task configurations and replace
    # environment variables. Source: https://pypi.org/project/envyaml/

    # Get the name of the JobRequest
    # Using the name, get the JobLauncher configuration
    #task_configuration = TASKS[job_request_node['name']]
    task_name = job_request_node['properties']['name']
    task_configuration = EnvYAML(f'tasks/{task_name}.yaml')

    #dsub_args = create_dsub_job_args(job_dict)
    dsub_args = create_dsub_job_args(task_configuration)
    logging.debug(f"> job-launcher: Dsub arguments = {dsub_args}.")

    # Optional flags
    if ENABLE_JOB_LAUNCH == 'false':
        dsub_args.append("--dry-run")
    
    # Launch dsub job
    """Commenting this all out for development
    logging.info(f"> job-launcher: Launching dsub with args: {dsub_args}.")
    dsub_result = launch_dsub_task(dsub_args)
    logging.info(f"> job-launcher: Dsub result: {dsub_result}.")
    """
    dsub_result = {'job-id': 'test'}

    if 'job-id' in dsub_result.keys():
        # Add dsub job ID to neo4j database node
        task_configuration['dsubJobId'] = dsub_result['job-id']
        task_configuration['dstatCmd'] = (
                                 "dstat " +
                                f"--project {job_dict['project']} " +
                                f"--provider {job_dict['provider']} " +
                                f"--jobs '{job_dict['dsubJobId']}' " +
                                f"--users '{job_dict['user']}' " +
                                 "--full " +
                                 "--format json " +
                                 "--status '*'")

        """I think I'm trying to hard with this. Just going to drop it for now.
        # Format inputs for neo4j database
        for key, value in task_configuration["dsub.inputs"].items():
            job_dict[f"input_{key}"] = value
        for key, value in job_dict["envs"].items():
            job_dict[f"env_{key}"] = value
        for key, value in job_dict["outputs"].items():
            job_dict[f"output_{key}"] = value
        """

        # Remove dicts from the job_dict because Neo4j can't handle them.
        del task_configuration["dsub.inputs"]
        del task_configuration["dsub.outputs"]
        del task_configuration["dsub.environment_variables"]
        del task_configuration["dsub.labels"]

        # Create query request
        query_request = trellis.QueryRequestWriter(
            sender = FUNCTION_NAME,
            seed_id = query_response.seed_id,
            previous_event_id = query_response.event_id,
            query_name = "createDsubJobNode",
            query_parameters = task_configuration)
        message = query_request.format_json_message()

        logging.info(f"> job-launcher: Pubsub message: {message}.")
        result = trellis.utils.publish_to_pubsub_topic(
                    publisher = PUBLISHER,
                    project_id = PROJECT_ID,
                    topic = TRELLIS_CONFIG['TOPIC_DB_QUERY'],
                    message = message) 
        logging.info(f"> job-launcher: Published message to {TRELLIS_CONFIG['TOPIC_DB_QUERY']} with result: {result}.")
