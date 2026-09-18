# Elastic Cloud Compute Benchmarking API

![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![AWS Lambda](https://img.shields.io/badge/AWS_Lambda-FF9900?style=flat&logo=awslambda&logoColor=white)
![Amazon EC2](https://img.shields.io/badge/Amazon_EC2-FF9900?style=flat&logo=amazonec2&logoColor=white)
![Amazon S3](https://img.shields.io/badge/Amazon_S3-569A31?style=flat&logo=amazons3&logoColor=white)
![Google Cloud](https://img.shields.io/badge/Google_App_Engine-4285F4?style=flat&logo=googlecloud&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-000000?style=flat&logo=flask&logoColor=white)

A serverless-vs-EC2 cost and performance benchmarking system built on AWS and Google Cloud, developed as coursework for a cloud computing module (University of Surrey, COMM034).

---

## What it does

The API is framed around a stock trading signal use case (buy/sell signals generated from real Yahoo Finance data) as a realistic workload for benchmarking two cloud compute strategies:

- **AWS Lambda** — serverless, event-driven parallel processing
- **AWS EC2** — dedicated on-demand compute

The system provisions and tears down both resource types on demand (`/warmup`, `/terminate`), runs parallelised simulations under variable load, and logs cost and latency for each to Amazon S3 for comparison. Deployed on Google App Engine via Flask + Gunicorn.

**Note:** the Value-at-Risk and profit/loss figures returned by the API are randomised placeholder values used to generate realistic-shaped load for benchmarking, not output from a real risk model. The engineering focus of this project is the cloud architecture and cost/performance comparison, not financial forecasting.

---

## Key result

Comparing an all-EC2 baseline against a hybrid EC2 + Lambda split (500 simulated users each) for the same workload, compute costs dropped from an estimated $1,248/month to $717.75/month, a **~42% reduction**, by offloading half the load to Lambda's serverless pricing.

---

## Architecture

- **Google App Engine** — hosts the Flask API
- **AWS Lambda** — serverless, event-driven simulation execution
- **AWS EC2** — on-demand instances for heavier compute
- **Amazon S3** — stores warmup logs, cost data, and analysis results
- Evaluated against NIST SP 800-145's cloud service model (on-demand self-service, broad network access, IaaS/PaaS, public cloud)

---

## Stack

Python · Flask · boto3 · yfinance · AWS Lambda · AWS EC2 · Amazon S3 · Google App Engine
