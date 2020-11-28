import contextlib
import datetime as dt
import json
import secrets
import time

import boto3


ACCOUNT_ID = "975596993436"

READ_ONLY_ROLE_ARN = "arn:aws:iam::975596993436:role/storage-read_only"
DEV_ROLE_ARN = "arn:aws:iam::975596993436:role/storage-developer"
ADMIN_ROLE_ARN = "arn:aws:iam::975596993436:role/storage-admin"


sts_client = boto3.client("sts")


def create_aws_client_from_role_arn(resource, *, role_arn):
    """
    Create an AWS client using the given role.
    """
    assumed_role_object = sts_client.assume_role(
        RoleArn=role_arn, RoleSessionName="AssumeRoleSession1"
    )
    credentials = assumed_role_object["Credentials"]
    return create_aws_client_from_credentials(resource, credentials=credentials)


def create_aws_client_from_credentials(resource, *, credentials):
    """
    Create an AWS client using the given credentials.
    """
    return boto3.client(
        resource,
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )


def get_underlying_role_arn():
    """
    Returns the original role ARN.
    e.g. at Wellcome we have a base role, but then we assume roles into different
    accounts.  This returns the ARN of the base role.
    """
    client = boto3.client("sts")
    return client.get_caller_identity()["Arn"]


@contextlib.contextmanager
def _temporary_role(admin_role_arn):
    """
    Create an IAM role which can be assumed by ``admin_role_arn``.

        with _temporary_role(admin_role_arn) as (temp_role_arn, temp_role_name):
            # do stuff

    The role is deleted when you exit the context manager.
    """
    iam_client = create_aws_client_from_role_arn("iam", role_arn=admin_role_arn)

    # Name for the temporary role.  Role names must be between 1 and 64 chars
    # long, and case insensitive.
    # See https://docs.aws.amazon.com/IAM/latest/APIReference/API_CreateRole.html
    temporary_role_name = dt.datetime.now().strftime("temporary_%Y-%m-%d_%H-%M-%S")

    # Create the temporary role.  This policy document describes who is allowed
    # to assume this role -- since this is a temporary role only meant to be
    # used in the current context, we limit it to the admin role.
    assume_role_policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": admin_role_arn},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    try:
        create_role_resp = iam_client.create_role(
            RoleName=temporary_role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role_policy_document),
            Description=f"A temporary role created by {__file__}",
        )

        yield (create_role_resp["Role"]["Arn"], temporary_role_name)
    finally:
        iam_client.delete_role(RoleName=temporary_role_name)


@contextlib.contextmanager
def temporary_iam_credentials(*, admin_role_arn, policy_document):
    """
    Creates a temporary IAM credentials to use a particular policy document.
    Requires an IAM role that:

    *   The caller is allowed to assume
    *   Has permission to manage IAM roles

    Use this function as a context manager:

        with temporary_iam_credentials(admin_role, policy_document) as credentials:
            # Do stuff with credentials

    It creates a temporary admin role with the right policy document, and then
    cleans up the role once you're finished (even if an exception is thrown
    while using the credentials).

    Our storage-dev and storage-admin roles have an explicit, blanket "Deny" on
    deleting any objects in our permanent S3 buckets and DynamoDB tables.
    This allows us to create a role with a tightly-scoped holepunch through
    these Deny policies.

    """
    iam_client = create_aws_client_from_role_arn("iam", role_arn=admin_role_arn)

    with _temporary_role(admin_role_arn) as (temporary_role_arn, temporary_role_name):
        # Now attach the policy to the temporary role.  Note that we add permission
        # to use the DescribeRegions permission, which we'll use to validate our
        # credentials (see below).
        temporary_policy_name = f"policy-{secrets.token_hex(6)}"

        policy_document["Statement"].append(
            {
                "Sid": "",
                "Effect": "Allow",
                "Action": ["ec2:DescribeRegions"],
                "Resource": ["*"],
            }
        )

        iam_client.put_role_policy(
            RoleName=temporary_role_name,
            PolicyName=temporary_policy_name,
            PolicyDocument=json.dumps(policy_document),
        )

        # Allowing the admin role to assume the temporary role needs symmetric
        # IAM permissions:
        #
        #   * The temporary role needs a rule "the admin role can assume me"
        #   * The admin role needs a rule "I can assume the temporary role"
        #
        # We created the first rule with the AssumeRolePolicyDocument parameter on
        # the temporary role; now add the second rule on the admin role.
        admin_role_name = admin_role_arn.split("/")[-1]
        admin_policy_name = f"assume-{temporary_role_name}"

        policy_document = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "sts:AssumeRole",
                    "Resource": temporary_role_arn,
                    "Effect": "Allow",
                }
            ],
        }

        iam_client.put_role_policy(
            RoleName=admin_role_name,
            PolicyName=admin_policy_name,
            PolicyDocument=json.dumps(policy_document),
        )

        sts_client = create_aws_client_from_role_arn("sts", role_arn=admin_role_arn)

        # Handle the credentials to the caller.  Regardless of whether the calling
        # code succeeds or throws an exception, make sure we clean up the temporary role.
        try:
            # IAM updates don't apply instantaneously, and there may be a short delay
            # before we can assume the new role.  Even a successful call may be followed
            # by a failed call as everything sorts itself out.
            #
            # If you don't wait, you may get an error:
            #
            #       botocore.exceptions.ClientError: An error occurred (InvalidAccessKeyId)
            #       when calling the [Operation] operation: The AWS Access Key Id you
            #       provided does not exist in our records.
            #
            # This shouldn't be running in a hot path -- it's a tool for experimenting --
            # so wait 15 seconds before retrieving credentials.
            time.sleep(15)

            assumed_role_credentials = sts_client.assume_role(
                RoleArn=temporary_role_arn, RoleSessionName="AssumeRoleSession1"
            )

            yield assumed_role_credentials["Credentials"]
        finally:
            iam_client.delete_role_policy(
                RoleName=admin_role_name, PolicyName=admin_policy_name
            )
            iam_client.delete_role_policy(
                RoleName=temporary_role_name, PolicyName=temporary_policy_name
            )
