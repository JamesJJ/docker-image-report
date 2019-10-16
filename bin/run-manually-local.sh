#!/bin/bash

IMAGE="$1"

[ -z "${IMAGE}" ] && exit 1

export ECR_CHECK_OWNER_TEAM_REGEX='^[A-Z0-9/]+$'

robot \
    --pythonpath "$(dirname "$0")/../lib" \
    --prerunmodifier disable.SuiteSetup \
    --prerunmodifier disable.SuiteTeardown \
    --name "Containerized Service Compliance" \
    --log /tmp/log_html \
    --report NONE \
    `# --console "quiet"` \
    --removekeywords "TAG:SECRET" \
    --flattenkeywords "NAME:*" \
    --critical "CRITICAL" \
    --noncritical "robot:exit" \
    --tagstatexclude "robot:exit" \
    --tagstatexclude "CRITICAL" \
    --logtitle "⌘ ${IMAGE}" \
    --variable "PUSHED_BY:$(whoami)" \
    --variable "PUSHED_DATE:$(date -u)" \
    --variable "ECR_LOGIN_ADDRESS:ecr.example.com" \
    --variable "ECR_USERNAME:ecr_user" \
    --variable "ECR_PASSWORD:ecr_password" \
    --variable "IMAGE:${IMAGE}" \
    "$(dirname "$0")/../robot/basic-test-suite.robot"

[ -f output.xml ] && rm output.xml

