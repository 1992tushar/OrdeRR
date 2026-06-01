# OrdeRR — Create All WhatsApp Templates
# Run this once when setting up on a new WhatsApp Business Account
# Replace TOKEN and WABA_ID before running

$TOKEN = "YOUR_META_ACCESS_TOKEN"
$WABA_ID = "YOUR_WABA_ID"

$headers = @{
    "Authorization" = "Bearer $TOKEN"
    "Content-Type"  = "application/json"
}

$templates = @(

@"
{
  "name": "salesperson_pending_orders",
  "language": "en",
  "category": "UTILITY",
  "components": [{
    "type": "BODY",
    "text": "Pending Orders - {{1}}\n\nHi {{2}}, the following customers have not placed their order yet:\n\n{{3}}\n\nTotal Pending: {{4}}\n\nPlease follow up with them.\n- {{1}} Team",
    "example": { "body_text": [["Fluffy", "Ritali", "1. Shubhada Hotel (Talegaon)", "1"]] }
  }]
}
"@,

@"
{
  "name": "manager_new_order",
  "language": "en",
  "category": "UTILITY",
  "components": [{
    "type": "BODY",
    "text": "New Order received at {{1}}.\n\nRestaurant: {{2}}\n\nItems:\n{{3}}\n\nPlease confirm and begin processing.",
    "example": { "body_text": [["Fluffy", "Hotel Sai Krupa - 919800000001", "1. Curry Cut - 5 kg\n2. Breast Boneless - 3 kg\nDelivery: As per usual schedule"]] }
  }]
}
"@,

@"
{
  "name": "manager_daily_summary",
  "language": "en",
  "category": "UTILITY",
  "components": [{
    "type": "BODY",
    "text": "Daily Order Status - {{1}}\nDate: {{2}}\n\nTotal Customers: {{3}}\nOrders Received: {{4}}\nPending Orders: {{5}}\n\nPending by Area:\n{{6}}\n\nOrdeRR - {{1}} Automation",
    "example": { "body_text": [["Fluffy", "31 May 2026", "3", "2", "1", "Talegaon (1 pending)\n  1. Shubhada Hotel"]] }
  }]
}
"@,

@"
{
  "name": "manager_daily_report",
  "language": "en",
  "category": "UTILITY",
  "components": [{
    "type": "BODY",
    "text": "Daily Report - {{1}}\nDate: {{2}}\n\nTotal Orders: {{3}}\nTotal Items: {{4}}\n\nProduct Summary:\n{{5}}\n\nOrdeRR - {{1}} Automation",
    "example": { "body_text": [["Fluffy", "31 May 2026", "3", "12", "1. Curry Cut - 15 kg\n2. Breast Boneless - 10 kg\n3. Wings - 5 kg"]] }
  }]
}
"@

)

$url = "https://graph.facebook.com/v21.0/$WABA_ID/message_templates"

foreach ($body in $templates) {
    try {
        $result = Invoke-RestMethod -Uri $url -Method POST -Headers $headers -Body $body
        Write-Host "✅ Created: $($result.id) — status: $($result.status)" -ForegroundColor Green
    } catch {
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        Write-Host "❌ Failed: $($reader.ReadToEnd())" -ForegroundColor Red
    }
}

Write-Host "`nDone! Check status with:"
Write-Host "Invoke-RestMethod -Uri `"https://graph.facebook.com/v21.0/$WABA_ID/message_templates?fields=name,status,id&limit=20`" -Method GET -Headers `$headers | Select-Object -ExpandProperty data"