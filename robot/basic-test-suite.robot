*** Settings ***
Library           Process
Library           String
Library           OperatingSystem
Library           JSONLibrary
Suite Setup       Suite-Setup
Suite Teardown    Suite-Teardown
Metadata          Pushed Date  ${PUSHED_DATE}
Metadata          Pushed By  ${PUSHED_BY}
Metadata          Image Address  ${IMAGE}


*** Variables ***
@{docker_run}  run  --no-healthcheck  --read-only  --rm  --network=none


*** Test Cases  ***

IMAGE ATTRIBUTES
    [Tags]  CRITICAL
    Image Label owner_team  ${IMAGE}
    Image Label base_image  ${IMAGE}
    Image Label com.azure.dev.image.build.repository.uri  ${IMAGE}

SECURITY
    [Tags]  CRITICAL  Security
    AWS Keys In Default Env  ${IMAGE}

SECURITY-UNENFORCED
    [Tags]  Security
    Container User Not Root  ${IMAGE}

JAVA
    [Tags]  CRITICAL  Java
    Java Version  ${IMAGE}
    Java XX MaxRAMPercentage  ${IMAGE}
    ReactiveMongo Version  ${IMAGE}

NODE
    [Tags]  CRITICAL  NodeJS
    NodeJS Version  ${IMAGE}

PYTHON
    [Tags]  Python
    Python Version  ${IMAGE}
    Python EOL Check  ${IMAGE}

LINUX VERSION
    [Tags]  CRITICAL  Base OS
    Alpine Version  ${IMAGE}
    Debian Version  ${IMAGE}

IMAGE EFFICIENCY
    [Tags]  Image Build
    Build Efficiency Dive  ${IMAGE}


*** Keywords ***


Suite-Setup
  [Tags]  SECRET
  ${RANDOM_SLUG} =  Generate Random String  12  [LOWER][NUMBERS]
  Set Global Variable  ${RANDOM_SLUG}  ${RANDOM_SLUG}
  Log  Image = ${IMAGE}  console=True
  ECR Login  ${ECR_LOGIN_ADDRESS}  ${ECR_USERNAME}  ${ECR_PASSWORD}
  Pull Image  ${IMAGE}
  Image Label ops_permit_dangerous  ${IMAGE}


Suite-Teardown
  Delete Image  ${IMAGE}
  Terminate All Processes    kill=True


Delete Image
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  rmi  ${IMAGE}


ECR Login
  [Tags]  SECRET
  [Arguments]   ${ECR_LOGIN_ADDRESS}  ${ECR_USERNAME}  ${ECR_PASSWORD}
  ${result} =   Run Process  docker  login  -u  ${ECR_USERNAME}  -p  ${ECR_PASSWORD}  ${ECR_LOGIN_ADDRESS}
  Run Keyword If  ${result.rc}!=0  Fatal Error


Pull Image
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  pull  ${IMAGE}
  Run Keyword If  ${result.rc}!=0  Log  ${result.stderr}  console=True
  Run Keyword If  ${result.rc}!=0  Fatal Error


Python Version
  [Tags]  Python
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  @{docker_run}  -i  --entrypoint  python  ${IMAGE}  -c  import sys;print(sys.version_info)
  Log  ${result.stdout}  console=True
  Pass Execution If  ${result.rc}!=${0}  Python not available.


Python EOL Check
  [Tags]  Python
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  @{docker_run}  -i  --entrypoint  python  ${IMAGE}  -c  import sys;print(sys.version_info.major)
  Should Not Match Regexp  ${result.stdout}  ^[0-2]$  Python is too old


Java Version
  [Tags]  Java
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  @{docker_run}  -i  --entrypoint  java  ${IMAGE}  -version
  Log  ${result.stderr}  console=True
  Pass Execution If  ${result.rc}!=${0}  Java not available.


Java XX MaxRAMPercentage
  [Tags]  Java
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  @{docker_run}  -i  --entrypoint  java  ${IMAGE}  -XX:MaxRAMPercentage\=80.0  -version
  Log  ${result.stderr}
  Should Be Equal As Numbers  ${result.rc}  0  Java too old


Alpine Version
  [Tags]  Base OS
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  @{docker_run}  -i  --entrypoint  cat  ${IMAGE}  /etc/alpine-release
  Log  Alpine = ${result.stdout}  console=True
  ${stripped} =  Strip String  ${result.stdout}
  Should Not Match Regexp  ${stripped}  ^[0-2]\\.
  Should Not Match Regexp  ${stripped}  ^3\\.[0-7]\\.
  

Debian Version
  [Tags]  Base OS
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  @{docker_run}  -i  --entrypoint  cat  ${IMAGE}  /etc/debian_version
  Log  Debian = ${result.stdout}  console=True
  ${stripped} =  Strip String  ${result.stdout}
  Should Not Match Regexp  ${stripped}  ^[0-8]\\.
  Should Not Match Regexp  ${stripped}  ^9\.[0-7]\\.


AWS Keys In Default Env
  [Tags]  Security
  [Arguments]   ${IMAGE}
  [Documentation]  The default environment in the container must not include AWS credentials
  ${result} =   Run Process  docker  inspect  -f  {{ .Config.Env }}  ${IMAGE}
  ${groups} =  Get Regexp Matches  ${result.stdout}  ((?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{8})[A-Z0-9]{8}  1
  Length Should Be  ${groups}  0  msg=Found possible AWS credential: ${groups} (truncated)


Container User Not Root
  [Tags]  Security
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  inspect  -f  {{ .Config.User }}  ${IMAGE}
  ${stripped} =  Strip String  ${result.stdout}
  Log  Container User = ${stripped}  console=True
  Run Keyword If  $stripped  Set Suite Metadata  Container User  ${stripped}  append=True  top=True
  ${whole_match} =  Should Not Match Regexp  ${stripped}  ^(|0:.+|.+:0|root|root:.+|.+:root)$  Container Runs As Root


Image Label ops_permit_dangerous
  [Tags]  SECRET
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  inspect  -f  {{ .Config.Labels.ops_permit_dangerous }}  ${IMAGE}
  ${stripped} =  Strip String  ${result.stdout}
  Run Keyword If  $stripped=="exigency"  Remove Tags  CRITICAL

Image Label owner_team
  [Tags]  Image Build
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  inspect  -f  {{ .Config.Labels.owner_team }}  ${IMAGE}
  ${stripped} =  Strip String  ${result.stdout}
  Log  Label owner_team = ${stripped}  console=True
  ${whole_match} =  Should Match Regexp  ${stripped}  ^[a-zA-Z0-9/]+$  Label missing or unacceptable
  Run Keyword If  $whole_match is not None  Set Suite Metadata  Image Owner Team  ${whole_match}  append=True  top=True


Image Label base_image
  [Tags]  Image Build
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  inspect  -f  {{ .Config.Labels.base_image }}  ${IMAGE}
  ${stripped} =  Strip String  ${result.stdout}
  Log  Label base_image = ${stripped}  console=True
  ### ${whole_match} =  Should Match Regexp  ${stripped}  ^[a-zA-Z0-9/]+$  Label missing or unacceptable
  Run Keyword If  $stripped  Set Suite Metadata  Base Image Label  ${stripped}  append=True  top=True


Image Label com.azure.dev.image.build.repository.uri
  [Tags]  Image Build
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  inspect  -f  {{ index .Config.Labels "com.azure.dev.image.build.repository.uri" }}  ${IMAGE}
  ${stripped} =  Strip String  ${result.stdout}
  ${stripped} =  Replace String Using Regexp  ${stripped}  ://[^@]+@  ://
  Log  Label com.azure.dev.image.build.repository.uri = ${stripped}  console=True
  ### ${whole_match} =  Should Match Regexp  ${stripped}  ^[a-zA-Z0-9/]+$  Label missing or unacceptable
  Run Keyword If  $stripped  Set Suite Metadata  Azure DevOps Repo  ${stripped}  append=True  top=True


ReactiveMongo Version
  [Tags]  Java
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  @{docker_run}  -i  --entrypoint  find  ${IMAGE}  --  /  -type  f  -iname  *reactivemongo_*-*.jar
  Log  ${result.stdout}
  ${first_line} =  Get Line  ${result.stdout}${\n}  0
  Log  ReactiveMongo = ${first_line}  console=True
  Should Not Match Regexp   ${first_line}  [_\-](0)\\.([0-9]|1[012])\\.([0-9]+)\\.jar$  ReactiveMongo Version Unacceptable


NodeJS Version
  [Tags]  NodeJS
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  docker  @{docker_run}  -i  --entrypoint  sh  ${IMAGE}  -c  node --version
  Log  ${result.stdout}
  Pass Execution If  ${result.rc}!=${0}  NodeJS not available


Build Efficiency Dive
  [Tags]  Image Build
  [Arguments]   ${IMAGE}
  ${result} =   Run Process  /usr/local/bin/dive  ${IMAGE}  -j  /tmp/dive-${RANDOM_SLUG}.txt
  Pass Execution If  ${result.rc}!=${0}  Dive not available
  Log  ${result.stdout}
  ${dive_json} =	 Load JSON From File  /tmp/dive-${RANDOM_SLUG}.txt
  ${dive_score} =  Get Value From JSON  ${dive_json}  $.image.efficiencyScore
  Remove File  /tmp/dive-${RANDOM_SLUG}.txt
  Log  Efficiency Score ${dive_score[0]}  console=True
  Should Be True  ${dive_score[0]}>0.80  Image is too ineffecient
  

