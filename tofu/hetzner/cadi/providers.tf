terraform {
  required_version = "~> 1.17.0"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.45"
    }
  }

  backend "s3" {
    endpoints                   = { s3 = "http://ds920plus.lan.eudald.gr:9000" }
    bucket                      = "tofu"
    key                         = "hetzner/cadi.tfstate"
    region                      = "eu-south-0"
    use_path_style              = true
    use_lockfile                = true
    skip_credentials_validation = true
    skip_region_validation      = true
  }
}

provider "hcloud" {}
