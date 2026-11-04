# Alerts for failures nothing else reports. The kg-graph handler catches query failures and returns
# them to the agent, so Lambda's own Errors metric stays 0 and Gateway reports isError: false. Only
# the status field in the handler's log line shows them, so a metric filter reads that line.
# A filter also avoids PutMetricData, which the Lambdas could not reach: their VPC has no route to it.

# The kg-graph log group is already managed as aws_cloudwatch_log_group.loader_lambda
# (terraform/loader_lambda.tf), imported there after Lambda auto-created it on first run.
resource "aws_cloudwatch_log_metric_filter" "kg_tool_errors" {
  name           = "${var.name}-kg-tool-errors"
  log_group_name = aws_cloudwatch_log_group.loader_lambda.name
  pattern        = "{ ($.event = \"kg_tool_call\") && ($.status = \"error\") }"

  metric_transformation {
    name      = "KgToolErrors"
    namespace = "MMRF/KnowledgeGraph"
    value     = "1"
    unit      = "Count"
  }
}

# Topic encryption is left off: alarms can't publish to a topic using the AWS managed SNS key, and
# these messages hold only alarm names and states.
resource "aws_sns_topic" "kg_alerts" {
  name = "${var.name}-alerts"
}

# AWS emails this address a confirmation link, and nothing is delivered until it is clicked.
resource "aws_sns_topic_subscription" "kg_alerts_email" {
  count     = var.alert_email == null ? 0 : 1
  topic_arn = aws_sns_topic.kg_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "kg_tool_errors" {
  alarm_name          = "${var.name}-kg-tool-errors"
  alarm_description   = "A kg-graph tool failed to query the graph. Search the kg-graph log group for status = error."
  namespace           = "MMRF/KnowledgeGraph"
  metric_name         = "KgToolErrors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.kg_alerts.arn]

  depends_on = [aws_cloudwatch_log_metric_filter.kg_tool_errors]
}

# Crashes, timeouts and failed loads, which do reach Lambda's Errors metric.
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = {
    kg-graph = aws_lambda_function.loader.function_name
    ingest   = aws_lambda_function.ingest.function_name
  }

  alarm_name          = "${var.name}-${each.key}-lambda-errors"
  alarm_description   = "${each.value} crashed or timed out. Check its log group."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.kg_alerts.arn]
}
