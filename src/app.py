import boto3
import os

# Initialize the DynamoDB client, pointing to the correct region for the table
# Note: Lambda@Edge itself runs from us-east-1, but it can call services in any region.
dynamodb = boto3.resource('dynamodb', region_name='us-east-2')

# Get table name from environment variables set by SAM template, with a fallback
table_name = os.environ.get('TABLE_NAME', 'geo-url-shortener')
table = dynamodb.Table(table_name)

def lambda_handler(event, context):
    """
    Handles viewer requests from CloudFront.
    - Extracts the short_id from the URL path.
    - Gets the viewer's country from headers.
    - Queries DynamoDB for the destination URL.
    - Returns an HTTP 302 redirect response.
    """
    request = event['Records'][0]['cf']['request']
    headers = request['headers']
    path = request['uri']

    # Ignore browser requests for the favorite icon
    if path.endswith('favicon.ico'):
        return { 'status': '204', 'statusDescription': 'No Content' }

    # Handle root path requests
    if path == "/" or len(path) <= 1:
        default_url = os.environ.get('DEFAULT_URL', 'https://github.com/aws-samples')
        return {
            'status': '302',
            'statusDescription': 'Found',
            'headers': {
                'location': [{'key': 'Location', 'value': default_url}]
            }
        }
    
    short_id = path.lstrip('/')

    # Get viewer's country code; defaults to 'default' if header is not present
    viewer_country = 'default'
    if 'cloudfront-viewer-country' in headers:
        viewer_country = headers['cloudfront-viewer-country'][0]['value']

    try:
        # Query DynamoDB for the short_id
        response = table.get_item(Key={'short_id': short_id})
        
        if 'Item' not in response:
            return {
                'status': '404',
                'statusDescription': 'Not Found',
                'body': f'Short URL for "{short_id}" not found.'
            }

        destinations = response['Item'].get('destinations', {})
        
        # Determine the final URL: country-specific or fallback to default
        destination_url = destinations.get(viewer_country, destinations.get('default'))

        if not destination_url:
             raise KeyError("A valid 'default' destination is required in the DynamoDB item.")

        # Return the 302 Redirect response
        return {
            'status': '302',
            'statusDescription': 'Found',
            'headers': {
                'location': [{'key': 'Location', 'value': destination_url}],
                'cache-control': [{'key': 'Cache-Control', 'value': 'private, max-age=0'}]
            }
        }

    except Exception as e:
        print(f"Error processing request for short_id '{short_id}': {str(e)}")
        return {
            'status': '500',
            'statusDescription': 'Internal Server Error',
            'body': 'An internal error occurred. Please check function logs.'
        }