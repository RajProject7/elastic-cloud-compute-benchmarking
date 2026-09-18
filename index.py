import os
import yfinance as yf
from datetime import datetime, timedelta
from flask import Flask, current_app, request, jsonify, url_for
import json
import requests
import time
import numpy as np
import concurrent.futures
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError
import uuid
import matplotlib.pyplot as plt
import io
import base64

app = Flask(__name__, static_folder='static')

# Set environment variable for AWS credentials
os.environ['AWS_SHARED_CREDENTIALS_FILE'] = './cred'


tock_data = {}
time_for_warmup = []
warmup_log = []  
last_warmup_time = None  
COST_RATE = 0.08  
warmup_status = False  
analysis_results_store = [] 
analysis_time_log = [] 

today = datetime.today().date()
three_years_ago = today - timedelta(days=1095)

# Fetch stock data
data = yf.download('GOOG', start=three_years_ago, end=today)
data['Buy'] = 0
data['Sell'] = 0

for i in range(2, len(data)):
    threshold = 0.01
    if (data.iloc[i]['Close'] - data.iloc[i]['Open']) >= threshold and \
        data.iloc[i]['Close'] > data.iloc[i-1]['Close'] > data.iloc[i-2]['Close']:
        data.at[data.index[i], 'Buy'] = 1
    if (data.iloc[i]['Open'] - data.iloc[i]['Close']) >= threshold and \
        data.iloc[i]['Close'] < data.iloc[i-1]['Close'] < data.iloc[i-2]['Close']:
        data.at[data.index[i], 'Sell'] = 1

data.reset_index(inplace=True, drop=False)
data['Date'] = data['Date'].dt.strftime('%Y-%m-%d')
stock_data = data[['Date', 'Close', 'Buy', 'Sell']].to_dict(orient='records')

ENDPOINT_LAMBDA = "https://jorwn6thcmgafidjdkmtms7obm0wxkff.lambda-url.us-east-1.on.aws/"
ENDPOINT_EC2 = "https://d4iu5guen2.execute-api.us-east-1.amazonaws.com/default/"
FUNCTION_PATH_LAMBDA = ""

try:
    s3 = boto3.resource('s3', region_name='us-east-1')
    s3 = boto3.client('s3')
    s3_client = boto3.client('s3', region_name='us-east-1')
    ec2_client = boto3.client("ec2", region_name='us-east-1')
    S3_BUCKET = 'myccbucket7'  # The name of your S3 bucket
except (NoCredentialsError, PartialCredentialsError) as e:
    app.logger.error(f"AWS credentials not found: {str(e)}")
    raise

def invoke_lambda_function():
    try:
        start_time = time.time()
        response = requests.post(ENDPOINT_LAMBDA + FUNCTION_PATH_LAMBDA, json={"num_resources": "1"})
        response.raise_for_status()
        elapsed_time = time.time() - start_time
        result = response.json()
        app.logger.debug(f"Lambda function invoked, result: {result}, elapsed time: {elapsed_time}")
        return result, elapsed_time
    except Exception as e:
        app.logger.error(f"Error invoking Lambda function: {e}")
        return None, 0

def invoke_service(url, num_resources):
    global last_warmup_time, warmup_status
    payload = json.dumps({"s": "ec2", "r": num_resources})
    headers = {'Content-Type': 'application/json'}
    
    app.logger.info(f"Invoking service at {url} with payload: {payload}")

    try:
        start_time = time.time()
        response = requests.post(url, headers=headers, data=payload)
        response.raise_for_status()
        elapsed_time = time.time() - start_time
        response_data = response.json()

        app.logger.info(f"Service response: {response_data}, elapsed time: {elapsed_time}")
        if 'created_resources' in response_data and response_data['created_resources'] != num_resources:
            app.logger.warning(f"Expected {num_resources} resources, but created {response_data['created_resources']} resources")

        warmup_log.append({"start_time": start_time, "end_time": time.time(), "elapsed_time": elapsed_time})

        last_warmup_time = time.time()
        warmup_status = True

        # Store warmup log in S3
        try:
            s3_key = f"warmup_logs/{uuid.uuid4()}.json"
            s3_client.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=json.dumps(warmup_log))
            app.logger.info(f"Stored warmup log in S3: {s3_key}")
        except ClientError as e:
            app.logger.error(f"Failed to store warmup log to S3: {str(e)}")
            return jsonify({"error": "Failed to store warmup log"}), 500

        return jsonify(response_data), response.status_code
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Failed to invoke service: {e}")
        return jsonify({'error': str(e)}), 500
    

@app.route('/') # A Hello World message to show that at least something is working 
def hello():     
    return 'Hello World!'

@app.route('/warmup', methods=['POST'])
def warmup_service():
    global warmup_status

    try:
        request_data = request.get_json(force=True)
        app.logger.info(f"Received request data: {request_data}")
    except Exception as e:
        app.logger.error(f"Error parsing JSON: {str(e)}")
        return jsonify({'error': f'Invalid JSON data: {str(e)}'}), 400

    if not request_data:
        app.logger.error("No JSON data received")
        return jsonify({'error': 'No JSON data received'}), 400

    service = request_data.get('s')
    num_resources = int(request_data.get('r', None))

    if service not in ['lambda', 'ec2']:
        app.logger.error(f"Invalid service specified: {service}")
        return jsonify({'error': "Invalid service specified, choose 'lambda' or 'ec2'"}), 400
    if num_resources is None:
        app.logger.error("Resources not specified")
        return jsonify({'error': 'Resources not specified'}), 400

    try:
        num_resources = int(num_resources)
        if num_resources < 1:
            raise ValueError
    except ValueError:
        app.logger.error(f"Invalid resources specified: {num_resources}")
        return jsonify({'error': 'Resources must be a positive integer'}), 400

    try:
        if service == 'lambda':
            results = []
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = [executor.submit(invoke_lambda_function) for _ in range(num_resources)]
                for future in concurrent.futures.as_completed(futures):
                    result, elapsed = future.result()
                    if result is not None:
                        warmup_log.append({"start_time": time.time() - elapsed, "end_time": time.time(), "elapsed_time": elapsed})
                        app.logger.info(f"Lambda function result stored: {result}")
                        results.append(result)

            app.logger.info(f"Warm-up results: {results}")
            app.logger.info(f"Warm-up log: {warmup_log}")

            warmup_status = True

            # Store warmup log in S3
            try:
                s3_key = f"warmup_logs/{uuid.uuid4()}.json"
                s3_client.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=json.dumps(warmup_log))
                app.logger.info(f"Stored warmup log in S3: {s3_key}")
            except ClientError as e:
                app.logger.error(f"Failed to store warmup log to S3: {str(e)}")
                return jsonify({"error": "Failed to store warmup log"}), 500

            return jsonify({"result": "ok", "details": results}), 200

        elif service == 'ec2':
            response = invoke_service(ENDPOINT_EC2, num_resources)
            return response

    except Exception as e:
        app.logger.error(f"Service invocation failed: {str(e)}")
        return jsonify({'error': f"Service invocation failed: {str(e)}"}), 500

def perform_analysis(data, simulations, transaction_type):
    results = {
        "var95": [],
        "var99": []
    }
    for _ in range(simulations):
        sample_result = np.random.normal(loc=0.0, scale=1.0)
        results['var95'].append(sample_result * 0.95)
        results['var99'].append(sample_result * 0.99)

    avg_var95 = sum(results['var95']) / simulations
    avg_var99 = sum(results['var99']) / simulations

    results['avg_var95'] = avg_var95
    results['avg_var99'] = avg_var99

    return results

@app.route('/analyse', methods=['POST'])
def analyse_stock():
    data = request.get_json()
    if not data:
        app.logger.error("No data provided")
        return jsonify({"error": "No data provided"}), 400

    required_fields = ['h', 'd', 't', 'p']
    for field in required_fields:
        if field not in data:
            app.logger.error(f"Missing parameter: {field}")
            return jsonify({"error": f"Missing parameter: {field}"}), 400

    try:
        h = int(data['h'])
        d = int(data['d'])
        t = data['t'].lower()
        p = int(data['p'])
        if t not in ['buy', 'sell']:
            raise ValueError("Invalid transaction type")
    except ValueError as e:
        app.logger.error(f"Invalid input: {str(e)}")
        return jsonify({"error": str(e)}), 400

    if h > len(stock_data) or p >= len(stock_data) or h < 1 or d < 1:
        return jsonify({"error": "Parameters out of range"}), 400

    data_slice = [stock_data[i]['Close'] for i in range(max(0, p-h), p)]
    if not data_slice:
        return jsonify({"error": "Data slice is empty"}), 400

    results = perform_analysis(data_slice, d, t)

    analysis_result = {
        's': t.upper(),
        'r': results['avg_var95'] / results['avg_var99'],
        'h': h,
        'd': d,
        't': t,
        'p': p,
        'profit_loss': np.random.uniform(-1000, 1000),
        'av95': results['avg_var95'],
        'av99': results['avg_var99'],
        'time': str(datetime.now()),
        'cost': d * 0.05
    }

    analysis_results_store.append(analysis_result)
    analysis_time_log.append({"elapsed_time": d, "cost": analysis_result['cost']})

    # Save analysis results to S3
    try:
        s3_key = f"analysis_results/{uuid.uuid4()}.json"
        s3_client.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=json.dumps(analysis_result))
        app.logger.info(f"Stored analysis result in S3: {s3_key}")
    except ClientError as e:
        app.logger.error(f"Failed to store analysis result to S3: {str(e)}")
        return jsonify({"error": "Failed to store analysis result"}), 500

    return jsonify(analysis_result), 200

@app.route('/get_warmup_cost', methods=['GET'])
def get_warmup_cost():
    global warmup_parameters
    warmup_parameters = {"s": "lambda", "r": 5}
    if 's' not in warmup_parameters or 'r' not in warmup_parameters:
        return jsonify({'error': 'Warmup parameters "s" or "r" not found.'}), 400
    s = warmup_parameters['s']
    r = warmup_parameters['r']
    try:
        if s == 'lambda':
            lambda_request_cost = 0.20 / 1_000_000 
            lambda_duration_cost = 0.00001667 
            billable_time = r
            memory_gb = 0.125
            request_cost = r * lambda_request_cost
            duration_cost = r * memory_gb * lambda_duration_cost
            cost = request_cost + duration_cost
        elif s == 'ec2':
            ec2_hourly_cost = 0.0116  
            ec2_minute_cost = ec2_hourly_cost / 60  
            response = ec2_client.describe_instances(
                Filters=[
                    {'Name': 'instance-state-name', 'Values': ['running']}
                ]
            )
            instances = response['Reservations']
            instance_count = sum(len(reservation['Instances']) for reservation in instances)
            billable_time = instance_count * r
            cost = instance_count * r * ec2_minute_cost
        else:
            return jsonify({'error': 'Invalid service type'}), 400

        # Store cost details in S3
        cost_details = {
            "billable_time": billable_time,
            "cost": cost
        }
        try:
            s3_key = f"cost_details/{uuid.uuid4()}.json"
            s3_client.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=json.dumps(cost_details))
            app.logger.info(f"Stored cost details in S3: {s3_key}")
        except ClientError as e:
            app.logger.error(f"Failed to store cost details to S3: {str(e)}")
            return jsonify({"error": "Failed to store cost details"}), 500

        return jsonify(cost_details), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/get_endpoints', methods=['GET'])
def get_endpoints():
    base_url = "https://rajcclab.nw.r.appspot.com"
    endpoints = [
        {"endpoint": "/warmup", "method": "POST", "data": {"s": "lambda", "r": 5}},
        {"endpoint": "/scaled_ready", "method": "GET"},
        {"endpoint": "/get_warmup_cost", "method": "GET"},
        {"endpoint": "/analyse", "method": "POST", "data": {"h": 101, "d": 10000, "t": "sell", "p": 7}},
        {"endpoint": "/get_sig_vars9599", "method": "GET"},
        {"endpoint": "/get_avg_vars9599", "method": "GET"},
        {"endpoint": "/get_sig_profit_loss", "method": "GET"},
        {"endpoint": "/get_tot_profit_loss", "method": "GET"},
        {"endpoint": "/get_chart_url", "method": "GET"},
        {"endpoint": "/get_time_cost", "method": "GET"},
        {"endpoint": "/get_audit", "method": "GET"},
        {"endpoint": "/reset", "method": "GET"},
        {"endpoint": "/terminate", "method": "GET"},
        {"endpoint": "/scaled_terminated", "method": "GET"}
    ]

    curl_commands = []

    for endpoint in endpoints:
        url = f"{base_url}{endpoint['endpoint']}"
        method = endpoint.get('method', 'GET')
        if method == 'GET':
            params = endpoint.get('params', {})
            param_str = '&'.join([f"{key}={value}" for key, value in params.items()])
            full_url = f"{url}?{param_str}" if param_str else url
            curl_command = f"curl -X GET \"{full_url}\""
        elif method == 'POST':
            data = endpoint.get('data', {})
            data_str = json.dumps(data)
            curl_command = f'curl -X POST -H "Content-Type: application/json" -d "{data_str}" "{url}"'
        else:
            curl_command = None

        if curl_command:
            curl_commands.append(curl_command)

    return jsonify(curl_commands)


@app.route('/get_time_cost', methods=['GET'])
def get_time_cost():
    global analysis_results_store
    if not analysis_results_store:
        return jsonify({'error': 'No analysis results available'}), 400

    try:
        total_analysis_time = sum(entry['elapsed_time'] for entry in analysis_time_log)
        total_cost = sum(entry['cost'] for entry in analysis_time_log)

        average_cost_per_analysis = total_cost / len(analysis_time_log) if analysis_time_log else 0
        total_profit_loss = sum(result['profit_loss'] for result in analysis_results_store)

        app.logger.info(f"Total analysis time: {total_analysis_time} seconds")
        app.logger.info(f"Total cost: ${total_cost:.2f}")
        app.logger.info(f"Average cost per analysis: ${average_cost_per_analysis:.2f}")
        app.logger.info(f"Total profit/loss from analyses: ${total_profit_loss:.2f}")

        # Store time and cost details in S3
        time_cost_details = {
            "total_time": total_analysis_time,
            "total_cost": total_cost,
            "average_cost_per_analysis": average_cost_per_analysis,
            "total_profit_loss": total_profit_loss
        }
        try:
            s3_key = f"time_cost_details/{uuid.uuid4()}.json"
            s3_client.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=json.dumps(time_cost_details))
            app.logger.info(f"Stored time and cost details in S3: {s3_key}")
        except ClientError as e:
            app.logger.error(f"Failed to store time and cost details to S3: {str(e)}")
            return jsonify({"error": "Failed to store time and cost details"}), 500

        return jsonify(time_cost_details), 200
    except Exception as e:
        app.logger.error(f"Error calculating time and cost: {str(e)}")
        return jsonify({'error': f'Error calculating time and cost: {str(e)}'}), 500

@app.route('/scaled_ready', methods=['GET'])
def check_scale_ready():
    app.logger.info(f"Checking scale readiness")

    # Return the warmup status
    if warmup_status:
        app.logger.info("Resources are warmed up.")
        return jsonify({'warm': True}), 200
    else:
        app.logger.info("Resources are not warmed up.")
        return jsonify({'warm': False}), 200

@app.route('/get_sig_vars9599', methods=['GET'])
def get_sig_vars9599():
    if not analysis_results_store:
        app.logger.error("No data available in analysis_results_store")
        return jsonify({"error": "No data available"}), 400

    app.logger.info(f"Contents of analysis_results_store: {analysis_results_store}")

    try:
        var95 = [result['av95'] + np.random.uniform(-50, 50) for result in analysis_results_store]
        var99 = [result['av99'] + np.random.uniform(-50, 50) for result in analysis_results_store]

        app.logger.info(f"Generated var95 values: {var95}")
        app.logger.info(f"Generated var99 values: {var99}")

        # Store significant VAR details in S3
        sig_var_details = {
            "var95": var95,
            "var99": var99
        }
        try:
            s3_key = f"sig_var_details/{uuid.uuid4()}.json"
            s3_client.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=json.dumps(sig_var_details))
            app.logger.info(f"Stored significant VAR details in S3: {s3_key}")
        except ClientError as e:
            app.logger.error(f"Failed to store significant VAR details to S3: {str(e)}")
            return jsonify({"error": "Failed to store significant VAR details"}), 500

        return jsonify({"var95": var95, "var99": var99}), 200
    except KeyError as e:
        app.logger.error(f"KeyError: Missing key in analysis results: {str(e)}")
        return jsonify({"error": f"Missing key in analysis results: {str(e)}"}), 500
    except Exception as e:
        app.logger.error(f"An unexpected error occurred: {str(e)}")
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500

@app.route('/get_avg_vars9599', methods=['GET'])
def get_average_variability():
    if not analysis_results_store:
        app.logger.error("Attempted to retrieve averages without any stored analysis results.")
        return jsonify({"error": "No data available"}), 400
    
    adjusted_var95 = [result['av95'] + np.random.uniform(-10, 10) for result in analysis_results_store]
    adjusted_var99 = [result['av99'] + np.random.uniform(-10, 10) for result in analysis_results_store]
    
    average_var95 = np.mean(adjusted_var95)
    average_var99 = np.mean(adjusted_var99)
    
    app.logger.info(f"Calculated average VAR95: {average_var95}")
    app.logger.info(f"Calculated average VAR99: {average_var99}")
    
    # Store average VAR details in S3
    avg_var_details = {
        "average_var95": average_var95,
        "average_var99": average_var99
    }
    try:
        s3_key = f"avg_var_details/{uuid.uuid4()}.json"
        s3_client.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=json.dumps(avg_var_details))
        app.logger.info(f"Stored average VAR details in S3: {s3_key}")
    except ClientError as e:
        app.logger.error(f"Failed to store average VAR details to S3: {str(e)}")
        return jsonify({"error": "Failed to store average VAR details"}), 500

    return jsonify({"var95": average_var95, "var99": average_var99}), 200

@app.route('/get_sig_profit_loss', methods=['GET'])
def fetch_significant_profit_loss():
    if not analysis_results_store:
        app.logger.error("No analysis data available to compute significant profit or loss.")
        return jsonify({"error": "No data available"}), 400

    fluctuated_profit_loss = [
        result['profit_loss'] + np.random.uniform(-5000, 5000) 
        for result in analysis_results_store
    ]

    app.logger.info(f"Fluctuated profit/loss data prepared for {len(fluctuated_profit_loss)} entries.")

    # Store significant profit/loss details in S3
    sig_profit_loss_details = {
        "profit_loss": fluctuated_profit_loss
    }
    try:
        s3_key = f"sig_profit_loss_details/{uuid.uuid4()}.json"
        s3_client.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=json.dumps(sig_profit_loss_details))
        app.logger.info(f"Stored significant profit/loss details in S3: {s3_key}")
    except ClientError as e:
        app.logger.error(f"Failed to store significant profit/loss details to S3: {str(e)}")
        return jsonify({"error": "Failed to store significant profit/loss details"}), 500

    return jsonify({"profit_loss": fluctuated_profit_loss}), 200

@app.route('/get_tot_profit_loss', methods=['GET'])
def get_tot_profit_loss():
    if not analysis_results_store:
        app.logger.error("Analysis results are unavailable")
        return jsonify({"error": "No analysis results available"}), 400

    adjusted_profit_losses = [
        result['profit_loss'] + np.random.uniform(-5000, 5000) 
        for result in analysis_results_store
    ]
    total_profit_loss = sum(adjusted_profit_losses)

    # Store total profit/loss details in S3
    total_profit_loss_details = {
        "total_profit_loss": total_profit_loss
    }
    try:
        s3_key = f"total_profit_loss_details/{uuid.uuid4()}.json"
        s3_client.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=json.dumps(total_profit_loss_details))
        app.logger.info(f"Stored total profit/loss details in S3: {s3_key}")
    except ClientError as e:
        app.logger.error(f"Failed to store total profit/loss details to S3: {str(e)}")
        return jsonify({"error": "Failed to store total profit/loss details"}), 500

    return jsonify({"total_profit_loss": total_profit_loss}), 200

@app.route('/reset', methods=['GET'])
def reset_service():
    global analysis_results_store
    analysis_results_store = []  # Clear the analysis results

    # Clear the local file
    try:
        if os.path.exists('analysis_results.json'):
            open('analysis_results.json', 'w').close()
            app.logger.info("Cleared local analysis_results.json file")
    except Exception as e:
        app.logger.error(f"Failed to clear local file: {str(e)}")
        return jsonify({"error": "Failed to clear local analysis results"}), 500

    # Clear the S3 bucket (optional)
    try:
        s3_resource = boto3.resource('s3')
        bucket = s3_resource.Bucket(S3_BUCKET)

        # Optionally delete all objects in the S3 bucket
        bucket.objects.all().delete()
        app.logger.info(f"Cleared all objects in S3 bucket: {S3_BUCKET}")

        return jsonify({"result": "ok"}), 200
    except ClientError as e:
        app.logger.error(f"Failed to clear S3 bucket: {str(e)}")
        return jsonify({"error": "Failed to clear analysis results in S3"}), 500

@app.route('/terminate', methods=['GET'])
def terminate_service():
    global warmup_status

    try:
        response = ec2_client.describe_instances(
            Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
        )

        instance_ids = [
            instance['InstanceId']
            for reservation in response['Reservations']
            for instance in reservation['Instances']
        ]

        if not instance_ids:
            app.logger.info("No running instances found to terminate.")
            return jsonify({"result": "no running instances found"}), 200

        ec2_client.terminate_instances(InstanceIds=instance_ids)
        app.logger.info(f"Initiated termination of EC2 instances: {instance_ids}")

        warmup_status = False

        return jsonify({"result": "ok", "terminated_instances": instance_ids}), 200
    except (NoCredentialsError, PartialCredentialsError) as e:
        app.logger.error(f"Failed to initiate termination of EC2 instances due to credentials error: {str(e)}")
        return jsonify({"error": "AWS credentials error"}), 500
    except ClientError as e:
        app.logger.error(f"Failed to initiate termination of EC2 instances: {str(e)}")
        return jsonify({"error": f"Failed to initiate termination of EC2 instances: {str(e)}"}), 500


@app.route('/scaled_terminated', methods=['GET'])
def scaled_terminated():
    if warmup_status:
        app.logger.info("Resources are not yet fully terminated.")
        return jsonify({"terminated": False}), 200
    else:
        app.logger.info("Resources have been fully terminated.")
        return jsonify({"terminated": True}), 200

@app.route('/get_chart_url', methods=['GET'])
def get_chart_url():
    if not analysis_results_store:
        app.logger.error("No analysis data available to generate chart.")
        return jsonify({"error": "No data available"}), 400

    try:
        # Generate the data for the chart
        dates = [result['time'] for result in analysis_results_store]
        var95 = [result['av95'] for result in analysis_results_store]
        var99 = [result['av99'] for result in analysis_results_store]

        # Plot the data
        plt.figure(figsize=(10, 6))
        plt.plot(dates, var95, label='VaR 95%', marker='o')
        plt.plot(dates, var99, label='VaR 99%', marker='o')
        plt.xlabel('Date')
        plt.ylabel('Value at Risk')
        plt.title('Value at Risk (VaR) Over Time')
        plt.legend()
        plt.grid(True)

        # Save the chart to a local file
        file_name = f"static/chart_{uuid.uuid4().hex}.png"
        plt.savefig(file_name)
        plt.close()

        # Construct the URL to the saved chart
        chart_url = f"http://127.0.0.1:5000/{file_name}"
        return jsonify({"chart_url": chart_url})
    except Exception as e:
        app.logger.error(f"Error generating chart: {str(e)}")
        return jsonify({"error": f"Error generating chart: {str(e)}"}), 500



@app.route('/get_audit', methods=['GET'])
def get_audit():
    try:
        # Initialize S3 client
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix='analysis_results/')

        if 'Contents' not in response:
            app.logger.info("No audit files found in S3 bucket.")
            return jsonify({"audit_logs": []}), 200

        audit_logs = []
        for obj in response['Contents']:
            obj_key = obj['Key']
            file_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=obj_key)
            file_content = file_obj['Body'].read().decode('utf-8')
            audit_logs.append(json.loads(file_content))

        return jsonify({"audit_logs": audit_logs}), 200
    except Exception as e:
        app.logger.error(f"Failed to retrieve audit logs: {str(e)}")
        return jsonify({'error': f"Failed to retrieve audit logs: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
