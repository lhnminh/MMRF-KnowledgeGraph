variable "daily_schedule_enabled" {
  description = "Enable the daily public-source refresh; manual runs remain available when disabled"
  type        = bool
  default     = true
}

variable "daily_schedule_hour_utc" {
  description = "Hour of the daily refresh in UTC (0-23); the minute is always 00"
  type        = number
  default     = 2
  validation {
    condition     = var.daily_schedule_hour_utc >= 0 && var.daily_schedule_hour_utc <= 23 && floor(var.daily_schedule_hour_utc) == var.daily_schedule_hour_utc
    error_message = "Use a whole UTC hour from 0 through 23."
  }
}

resource "aws_iam_role" "schedule" {
  name = "${var.name}-schedule"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRole",
      Principal = { Service = "scheduler.amazonaws.com" },
      # Scheduler supplies its schedule-group ARN, rather than an individual schedule ARN.
      Condition = { StringEquals = {
        "aws:SourceAccount" = var.account_id,
        "aws:SourceArn"     = "arn:aws:scheduler:${var.region}:${var.account_id}:schedule-group/default"
      } }
    }]
  })
}

resource "aws_iam_role_policy" "schedule" {
  name = "invoke-source-fetch-only"
  role = aws_iam_role.schedule.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow", Action = "lambda:InvokeFunction",
      Resource = aws_lambda_function.pipeline["fetch"].arn
    }]
  })
}

resource "aws_scheduler_schedule" "daily" {
  name                         = "${var.name}-daily"
  group_name                   = "default"
  description                  = "Daily Multiple Myeloma public sources to S3 and validated Neptune refresh"
  schedule_expression          = "cron(0 ${var.daily_schedule_hour_utc} * * ? *)"
  schedule_expression_timezone = "UTC"
  state                        = var.daily_schedule_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.pipeline["fetch"].arn
    role_arn = aws_iam_role.schedule.arn
    # execution-id changes on each attempt. The schedule ARN + scheduled time
    # instead gives retries one stable snapshot, and each new day a fresh run.
    # Keep the markers literal: jsonencode escapes angle brackets in JSON strings.
    input = <<-JSON
      {
        "id": "<aws.scheduler.schedule-arn>#<aws.scheduler.scheduled-time>",
        "source": "aws.scheduler",
        "scheduled_time": "<aws.scheduler.scheduled-time>"
      }
    JSON
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }
  }

  depends_on = [aws_iam_role_policy.schedule]
}

output "daily_schedule" {
  value = {
    name       = aws_scheduler_schedule.daily.name
    arn        = aws_scheduler_schedule.daily.arn
    expression = aws_scheduler_schedule.daily.schedule_expression
    timezone   = aws_scheduler_schedule.daily.schedule_expression_timezone
    enabled    = var.daily_schedule_enabled
  }
}
