#!/usr/bin/env python

import boto3

from iam_tester import create_aws_client_from_credentials, temporary_iam_credentials


if __name__ == "__main__":
    admin_role_arn = "arn:aws:iam::760097843905:role/platform-admin"

    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "s3:List*",
                "Resource": "*"
            },
            {
                "Effect": "Deny",
                "Action": "s3:List*",
                "Resource": "arn:aws:s3:::wellcomecollection-platform-infra"
            },
        ],
    }

    with temporary_iam_credentials(admin_role_arn=admin_role_arn, policy_document=policy_document) as credentials:
        s3_client = create_aws_client_from_credentials("s3", credentials=credentials)

        # We can list objects in any bucket except platform-infra.
        s3_client.list_objects_v2(Bucket="wellcomecollection-platform-dashboard")

        try:
            s3_client.list_objects_v2(Bucket="wellcomecollection-platform-infra")
        except Exception:
            pass
        else:
            assert False, "This ListObjects call did not fail!"