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
        gzip \
        psmisc \
        jq \
        python3 \
        python3-pip \
        openssl \
    && apt-get autoremove -y --purge \
    && apt-get -y clean

# Better to run this under an init: Add Tini
RUN \
curl -sSfL -o /usr/local/bin/tini https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini-static && \
chmod 755 /usr/local/bin/tini

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

RUN adduser --disabled-login --home /opt --shell /sbin/nologin --no-create-home  app_daemon

RUN pip3 install --no-cache-dir --upgrade docker
RUN pip3 install --no-cache-dir --upgrade boto3
RUN pip3 install --no-cache-dir --upgrade markdown

# Create a "python" command, because some old scripts may expect it
RUN bash -c 'which python || ln -s "$(which python3)" "/usr/bin/python"'

RUN echo 'set -o vi' | tee -a /root/.bashrc

COPY . /opt/imagecheck

WORKDIR /opt/imagecheck

CMD ["/usr/local/bin/tini","-g","python3","bin/check.py"]




