import json
import random
import boto3
import uuid

# Hardcoded bucket name
s3_bucket = 'myccbucket7'

# Initialize the S3 client
s3 = boto3.client('s3')

def lambda_handler(event, context):
    required_keys = {'Close', 'Buy', 'Sell', 'Date', 'h', 'd', 't'}
    if not required_keys.issubset(event.keys()):
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Missing required data keys'}),
            'headers': {
                'Content-Type': 'application/json'
            }
        }

    close_prices = event['Close']
    buy_signals = event['Buy']
    sell_signals = event['Sell']
    dates = event['Date']
    h = event['h']
    d = event['d']
    t = event['t'].lower()
    
    results = []

    if len(close_prices) < h:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Not enough data points'}),
            'headers': {
                'Content-Type': 'application/json'
            }
        }

    signals = buy_signals if t == 'buy' else sell_signals

    historical_returns = [(close_prices[i] - close_prices[i-1]) / close_prices[i-1] for i in range(1, len(close_prices))]
    
    for i in range(h, len(close_prices)):
        if signals[i] == 1:  
            var95, var99 = calculate_var(historical_returns[i-h:i], close_prices[i], d)
            results.append({
                'date': dates[i],
                'var95': var95,
                'var99': var99
            })

    # Prepare result data
    result_data = {
        'result': 'ok',
        'var_results': results
    }

    # Store result in S3
    try:
        # Define the file key
        s3_key = f"results/{uuid.uuid4()}.json"

        # Upload the data to S3
        s3.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=json.dumps(result_data),
            ContentType='application/json'
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({'result': 'ok', 'message': 'Data stored in S3', 's3_key': s3_key}),
            'headers': {
                'Content-Type': 'application/json'
            }
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Failed to store analysis result in S3', 'message': str(e)}),
            'headers': {
                'Content-Type': 'application/json'
            }
        }

def calculate_var(returns, current_price, shots):
    mean = sum(returns) / len(returns)
    std = (sum((x - mean) ** 2 for x in returns) / len(returns)) ** 0.5
    
    simulated_returns = sorted(random.gauss(mean, std) for _ in range(shots))
    
    var95 = simulated_returns[int(len(simulated_returns) * 0.05)]
    var99 = simulated_returns[int(len(simulated_returns) * 0.01)]
    
    var95_value = current_price * var95
    var99_value = current_price * var99
    
    return var95_value, var99_value
