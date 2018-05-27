# ECR Image Check

*This is an experiment to analyze Docker images as they are pushed to an image registry.*

Using [AWS Elastic Container Registry](https://aws.amazon.com/ecr/) as a [Docker](https://www.docker.com/) image registry, image push events logged by [Cloudwatch](https://aws.amazon.com/cloudwatch/) are sent to a [SQS queue](https://aws.amazon.com/sqs/).

The Python script here reads events from SQS, pulls the image from the docker registry, and executes some simple checks. Markdown and HTML reports are produced and uploaded to [S3](https://aws.amazon.com/s3/). Status notifications including a [presigned web link](https://docs.aws.amazon.com/AmazonS3/latest/dev/ShareObjectPreSignedURL.html) to view the HTML report are sent to [Microsoft Teams](https://products.office.com/en-us/microsoft-teams/group-chat-software).

## Current checks include:

 * Container labels
 * Java version (of `java` executable in path)
 * Java support for `-XX:+UseCGroupMemoryLimitForHeap`
 * Version of Scala [ReactiveMongo](http://reactivemongo.org/) library (parsed from JAR filenames, so does not currently handle a [fat JAR](https://www.google.com.tw/search?q=java+fat+jar)
 * NodeJS version (of `node` executable in path)
 * A feeble attempt at detecting the Linux distro and version inside the container
 * Image history, with reformatting to be more easily read by engineers, not as a 100% syntactically correct command list

## The HTML report includes:

 * The results from the checks listed above
 * A customizable logo image
 * Beautiful CSS formating, using the excellent [Tacit CSS Framework](https://github.com/yegor256/tacit)

### Configuration environment variables

*... TBC ...*

(`$ grep 'os.env' bin/check.py`)
