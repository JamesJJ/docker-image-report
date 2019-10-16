ARG BASE_IMAGE=debian:stretch-slim

FROM $BASE_IMAGE

ARG BASE_IMAGE

LABEL base_image=$BASE_IMAGE

ARG APP_CONFIG_BUILD_DATE
ENV APP_CONFIG_BUILD_DATE ${APP_CONFIG_BUILD_DATE:-unknown}
ARG APP_CONFIG_VERSION
ENV APP_CONFIG_VERSION ${APP_CONFIG_VERSION:-unknown}
ENV DEBIAN_FRONTEND noninteractive
ARG TINI_VERSION="v0.18.0"
ARG DOCKER_STATIC_TGZ="https://download.docker.com/linux/static/stable/x86_64/docker-19.03.1.tgz"

#  -------- Begin: Include updates -------
RUN if test -f /etc/debian_version; then apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* ;fi
RUN if test -f /etc/alpine-release; then apk upgrade --no-cache -v && apk add --no-cache curl; fi
#  -------- End: Include updates -------


RUN \
    apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        bash \
        bsdtar \
        gzip \
        psmisc \
        jq \
        python3 \
        python3-pip \
        python3-setuptools \
        openssl \
    && apt-get autoremove -y --purge \
    && apt-get -y clean

# Better to run this under an init: Add Tini
RUN \
curl -sSfL -o /usr/local/bin/tini https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini-static && \
chmod 755 /usr/local/bin/tini

RUN \
  curl -sSfL "${DOCKER_STATIC_TGZ}" \
  | bsdtar -xzv -C /usr/local/bin --strip-components 1 -f -  docker/docker \
  && chmod 755 /usr/local/bin/docker

#ENV DOCKERIZE_VERSION v0.6.0
#RUN curl -sSfLO "https://github.com/jwilder/dockerize/releases/download/$DOCKERIZE_VERSION/dockerize-linux-amd64-$DOCKERIZE_VERSION.tar.gz" \
#    && tar -C /usr/local/bin -xzvf "dockerize-linux-amd64-$DOCKERIZE_VERSION.tar.gz" dockerize \
#    && rm "dockerize-linux-amd64-$DOCKERIZE_VERSION.tar.gz"

# Install DIVE
ENV DIVE_VERSION 0.7.2
RUN \
  curl -fOL '-#' "https://github.com/wagoodman/dive/releases/download/v${DIVE_VERSION}/dive_${DIVE_VERSION}_linux_amd64.tar.gz" \
  && tar -C /usr/local/bin -xzvf "dive_${DIVE_VERSION}_linux_amd64.tar.gz" dive \
  && rm "dive_${DIVE_VERSION}_linux_amd64.tar.gz"

RUN useradd -d /opt -s /sbin/nologin  --no-create-home app_daemon

COPY pip-requirements.txt /tmp/

RUN pip3 install --no-cache-dir --upgrade -r /tmp/pip-requirements.txt

COPY . /opt/imagecheck

WORKDIR /opt/imagecheck

CMD ["/usr/local/bin/tini","-g","python3","bin/check.py"]

ENV ECR_CHECK_OWNER_TEAM_REGEX ^[A-Z0-9/]+$

