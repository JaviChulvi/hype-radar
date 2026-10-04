terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

# Lightsail distributions are global resources, but their API is available only in us-east-1.
provider "aws" {
  alias  = "global"
  region = "us-east-1"

  default_tags {
    tags = local.common_tags
  }
}
