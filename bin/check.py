#!/usr/bin/env python
# -*-  coding: utf-8 -*-

from __future__ import print_function

import sys
import os
import json
import base64
import contextlib
import urllib
import boto3
import requests
from requests.exceptions import RequestException
import logging
import re
import time
import string
import datetime
import random
import robot
from string import Template
from pprint import pformat as pf


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        else:
            return json.JSONEncoder.default(self, obj)


def lh(name, level=os.environ.get("LOG_LEVEL", "INFO")):
    logger = logging.getLogger(name)
    logger.setLevel("DEBUG")

    ch = logging.StreamHandler()
    ch.setLevel(level)

    # create formatter
    formatter = logging.Formatter("[%(levelname).4s] %(message)s")

    # add formatter to ch
    ch.setFormatter(formatter)

    # add ch to logger
    logger.addHandler(ch)
    return logger


def this_version_string():
    return json.dumps(
        {"version": str(os.getenv("APP_CONFIG_VERSION", "unknown"))},
        indent=None,
        sort_keys=True,
    )


def receive_sqs(queue_url):

    try:
        sqs = boto3.client(
            "sqs",
            region_name=os.environ.get("ECR_REGION", "us-west-2"),
            aws_access_key_id=os.environ.get("ECR_ACCESS_KEY", None),
            aws_secret_access_key=os.environ.get("ECR_SECRET_KEY", None),
            aws_session_token=os.environ.get("ECR_SESSION_TOKEN", None),
        )
        events = sqs.receive_message(
            QueueUrl=queue_url,
            WaitTimeSeconds=20,
            MaxNumberOfMessages=10,
            VisibilityTimeout=4000,
            MessageAttributeNames=["All"],
        )
    except Exception as e:
        logger.error(" = SQS Error: {}".format(e))
        return

    receipt_handle = None
    for event in events.get("Messages", []):
        try:
            logger.debug(" = Decapsulating JSON from Cloudwatch Event")
            body = json.loads(event.get("Body", "{}"))
            receipt_handle = event.get("ReceiptHandle")
            detail = body.get("detail", {})
            if event_routing(detail) is True:
                sqs.delete_message(ReceiptHandle=receipt_handle, QueueUrl=queue_url)
        except Exception as e:
            logger.error(e, exc_info=True)
            if receipt_handle:
                logger.warn(" = Deleting SQS ({!s})".format(receipt_handle))
                sqs.delete_message(ReceiptHandle=receipt_handle, QueueUrl=queue_url)
            continue


def event_routing(event_in):
    rt = {"ecr.amazonaws.com": handle_ecr_global}
    func = rt.get(event_in.get("eventSource", ""), False)
    if func:
        return func(event_in)
    return True


def handle_ecr_global(event_in):
    if event_in.get("eventName", "_") not in ("PutImage"):
        return True
    if event_in.get("errorCode", "_") in ("ImageAlreadyExistsException"):
        logger.info(
            " = Image already exists ({!s})".format(event_in.get("errorMessage", ""))
        )
        return True

    if verbose:
        logger.debug(" = EcrInput: {}".format(pf(event_in)))

    repo = event_in.get("requestParameters", {}).get("repositoryName")
    logger.info(" = Repo: {}".format(pf(repo)))

    tag = event_in.get("requestParameters", {}).get("imageTag")
    logger.info(" = Tag: {}".format(pf(tag)))

    registry_id = event_in.get("requestParameters", {}).get("registryId")
    logger.debug(" = RegId: {}".format(pf(registry_id)))

    registry_region = event_in.get("awsRegion", "us-east-1")
    logger.debug(" = RegRegion: {}".format(pf(registry_region)))

    event_time = event_in.get(
        "eventTime", datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")
    )
    logger.debug(" = EventTime: {}".format(pf(event_time)))

    user_id_base = event_in.get("userIdentity", {})
    user_id = user_id_base.get("userName", user_id_base.get("arn", "*Unknown*"))
    logger.debug(" = UserId: {}".format(pf(user_id)))

    handle_image(
        registry_id, registry_region, repo, tag, user_id, event_time.replace("T", " ")
    )
    return True


def handle_image(
    registry_id,
    registry_region,
    repo,
    tag,
    who=None,
    event_time=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ"),
):

    logger.debug(" = Running")

    dry_run = True if os.environ.get("DRY_RUN", "true") == "true" else False

    registry_address = os.environ.get(
        "ECR_REGISTRY_URL_FORMAT",
        "{registry_id}.dkr.ecr.{registry_region}.amazonaws.com",
    ).format(registry_id=registry_id, registry_region=registry_region)

    if str(tag) == "":
        tag = "latest"

    if tag == "latest" or tag == "":
        if os.environ.get("SKIP_UNTAGGED", "true") == "true":
            logger.info(' = Skipping "un-tagged" image: {}'.format(pf(repo)))
            return True

    # get ECR login
    ecrc = boto3.client(
        "ecr",
        region_name=registry_region,
        aws_access_key_id=os.environ.get("ECR_ACCESS_KEY", None),
        aws_secret_access_key=os.environ.get("ECR_SECRET_KEY", None),
        aws_session_token=os.environ.get("ECR_SESSION_TOKEN", None),
    )
    ecrauth = ecrc.get_authorization_token(registryIds=[registry_id])

    registry_username, registry_password = (
        base64.b64decode(
            ecrauth.get("authorizationData", [])[0].get(
                "authorizationToken", "user:pass"
            )
        )
        .decode("utf-8")
        .split(":")
    )

    logger.debug(
        ' = ECR Credentials: "{}" / "{}" (truncated to 12)'.format(
            registry_username[:12], registry_password[:12]
        )
    )

    log_html = "/tmp/log-{}.html".format(rid(12))
    robot_result = robot.run(
        "/opt/imagecheck/robot/basic-test-suite.robot",
        name="Containerized Service Compliance",
        log=log_html,
        report=None,
        output="/dev/null",
        stdout=None,
        stderr=None,
        console="quiet",
        removekeywords=["TAG:SECRET"],
        flattenkeywords=["NAME:*"],
        critical="CRITICAL",
        noncritical="robot:exit",
        tagstatexclude=["robot:exit", "CRITICAL"],
        logtitle="⌘ {}: {}".format(repo, tag),
        variable=[
            "PUSHED_BY:{}".format(who),
            "PUSHED_DATE:{}".format(event_time),
            "ECR_LOGIN_ADDRESS:https://{}".format(registry_address),
            "ECR_USERNAME:{}".format(registry_username),
            "ECR_PASSWORD:{}".format(registry_password),
            "IMAGE:{}/{}:{}".format(registry_address, repo, tag),
        ],
    )
    logger.debug(" = Robot result: {} ({})".format(pf(robot_result), log_html))

    url = None
    try:
        url = put_report_s3_presign(inject_custom_html(log_html))
    except Exception as e:
        logger.error(" = put_report_s3_presign: {}".format(pf(e)))
        url = None

    should_delete_image = False if robot_result == 0 else True

    colours = {
        "ok": "#98ef8d",  # Screaming Green
        "warn": "#ffd000",  # Bright Yellow
        "concern": "#ff7400",  # Strong Orange
        "critical": "#ff003e",  # Danger Red
    }

    at_title = "⌘ {}:{}".format(repo, tag)

    at_colour = (
        colours["warn"]
        if (should_delete_image and dry_run)
        else colours["critical"]
        if should_delete_image
        else colours["ok"]
    )

    at_summary = (
        "Failed Compliance Tests"
        if (should_delete_image and dry_run)
        else "Failed Compliance Tests => Image Deleted"
        if should_delete_image
        else "Passed Compliance Tests"
    )

    at_msg = (
        "**Failed Compliance Tests**"
        if (should_delete_image and dry_run)
        else "**Failed Compliance Tests**\n\n * Image Deleted"
        if should_delete_image
        else "*Passed Compliance Tests*"
    )

    try:
        teams_webhooks_delete = json.loads(os.environ.get("TEAMS_DELETE_URLS", []))
        teams_webhooks_warning = json.loads(os.environ.get("TEAMS_WARNING_URLS", []))
        teams_webhooks_ok = json.loads(os.environ.get("TEAMS_OK_URLS", []))
    except Exception as e:
        logger.error(" = Error parsing Teams URLs: {}".format(e))

    teams_webhooks = (
        teams_webhooks_delete
        if should_delete_image
        else teams_webhooks_warning
        if (should_delete_image and dry_run)
        else teams_webhooks_ok
    )

    alert_teams(at_title, at_colour, at_summary, at_msg, teams_webhooks, url)

    if should_delete_image and not dry_run:
        try:
            logger.debug(" = ECR Delete: {}".format(image.id))
            del_tag = ecrc.batch_delete_image(
                registryId=registry_id,
                repositoryName=repo,
                imageIds=[{"imageTag": tag}],
            )
            logger.debug(" = ECR DeleteTag: {}".format(pf(del_tag)))
            del_id = ecrc.batch_delete_image(
                registryId=registry_id,
                repositoryName=repo,
                imageIds=[{"imageDigest": image.id}],
            )
            logger.debug(" = ECR DeleteId: {}".format(pf(del_id)))
        except Exception as e:
            logger.error(" = ECR Delete Error: {}".format(e))


def inject_custom_html(file_in):
    pre_header_injection = """
        <div style="width: 65em; display: block; margin:  6px 0; padding-right: 0;">
          <img src="{}" style="display: block; height: 60px; float: right;">
        </div>
    """.format(
        os.environ.get(
            "LOGO_URL",
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAQAAADZc7J/AAACgklEQVRIx42VT0hUURTGv7SBnMkIcSKpnCEoW8wuhEgIXQTlGLjTlbQxRgj6J4xLhUgHFy3ClUSJr5ULCQqajc5GYtzUasaNGL2wxaBggyYOzq/F/HvP9940567eeef77jnn3vsd6YSFhBTsiY4lppPzGcNcMF9m+pOBhKIKqr5dULNQa3g0vpj+ns9xyDEABbZZ4GZeacUV9oQ/V0QKDMeWstsUcdomDxDKKqaACxwNKBCaMjYKeJpJD0IFGeo8AX+mAXXcmEvtUt++0IoQSqnLVntEgdBcKu8COeLI8vWX/hIBSlmyaJYCU4bb7qsMMsiqxTNbIUBGtRdoeMyt9hzdCNFNrupb5nSFoKBY+dxbw0tZt4q3uIwQV9iq+j7jq+WQVVgSGo1vu7bsiHFaaGHc0of3nKoRoLgkBRfTRY+u77PCCvsWz2MrHKUVVE/0W54G7QfX7QR5RTWWyDUILzJhhyOU0HTysEGCd5xzEiQ1nzluALzDNOedcJSR8csbtofJJl95zW2a3ODI9CA4Zo0n3OIql9wStxK4lbDLBG31YNYSnE3c42Fj4FITncf4yn7b6q8Zx0XaINQ4PK+o4yq/sQT46aMPvzdBWsETj6nISPW3j1kOOGDW+gLtK+54zkc1zSGMCcBPwu7wbFmj0VCsIigFBqsB7awDsE67G7wiKCVJmzR2yjm8sAT1sswyve771yQtqIj8nRVR/cQZS5jPq/6UTdqfakAdXXOpHeAP9/5/fHZZrwwWf+eksVGAtbIWeiy3wVLKIiIFhmJL2d985JoX3Gu0lXrRJHQ2/Cj+If02f582mu23rv5wtY/3u9GRxJ3kxYzPlKmMkprxGu//AKfspPPYrnsnAAAAAElFTkSuQmCC",
        )
    )

    file_out = "{}-injected.html".format(file_in)
    with open(file_in, "rt", encoding="utf-8") as fin:
        with open(file_out, "wt", encoding="utf-8") as fout:
            for line in fin:
                fout.write(
                    line.replace(
                        '<div id="header">',
                        '{}<div id="header">'.format(pre_header_injection),
                    )
                )
    return file_out


def put_report_s3_presign(file_name):
    s3bucket = os.environ.get("REPORT_BUCKET", "s3-bucket.example.com")
    s3key = "report/{}{}.html".format(
        rid(4), datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    )
    s3c = boto3.client("s3", region_name="us-west-2")
    response = s3c.upload_file(
        file_name,
        s3bucket,
        s3key,
        ExtraArgs={
            "ACL": "bucket-owner-full-control",
            "CacheControl": "private, no-cache, no-store",
            "ContentType": "text/html",
            "ServerSideEncryption": "AES256",
            "StorageClass": "STANDARD",
        },
    )
    psurl = s3c.generate_presigned_url(
        "get_object", Params={"Bucket": s3bucket, "Key": s3key}, ExpiresIn=86400 * 7
    )
    logger.info(" = REPORT: {}".format(psurl))
    return psurl


def rid(size=6, chars=string.ascii_uppercase):
    return "".join(random.choice(chars) for _ in range(size))


def alert_teams(
    title,
    colour="",
    summary="",
    msg="",
    teams_webhooks=[],
    url=None,
    ms_teams_proxy=None,
):

    body = msg

    try:
        body = body.decode("utf-8")
    except AttributeError:
        pass
    try:
        title = title.decode("utf-8")
    except AttributeError:
        pass
    try:
        summary = summary.decode("utf-8")
    except AttributeError:
        pass
    try:
        url = url.decode("utf-8")
    except AttributeError:
        pass

    headers = {"content-type": "application/json"}
    # set https proxy, if it was provided
    proxies = {"https": ms_teams_proxy} if ms_teams_proxy else None
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": summary,
        "title": title,
        "text": body,
        "potentialAction": [
            {
                "@type": "OpenUri",
                "name": "Show Compliance Report (available until {})".format(
                    re.sub(
                        r"(^| )0",
                        r"\1",
                        (datetime.datetime.utcnow() + datetime.timedelta(days=7))
                        .strftime("%m/%d %I%p")
                        .lower(),
                    )
                ),
                "targets": [{"os": "default", "uri": url}],
            }
        ],
    }
    if colour != "":
        payload["themeColor"] = colour

    for url in teams_webhooks:
        try:
            response = requests.post(
                url,
                data=json.dumps(payload, cls=DateTimeEncoder),
                headers=headers,
                proxies=proxies,
            )
            response.raise_for_status()
        except RequestException as e:
            logger.error("Error posting to ms teams: %s" % e)
        logger.debug(" = Teams response: {!s}".format(response))


if __name__ == "__main__":

    verbose = False

    logger = lh(__name__)

    logger.info("STARTING: {0}".format(this_version_string()))

    sqs_queue = os.environ.get("SQS_QUEUE_URL", None)

    while True:
        time.sleep(0.1)
        receive_sqs(sqs_queue)
