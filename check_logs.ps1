$logs = Invoke-RestMethod -Uri "http://localhost:5000/api/logs"
$logs | Select-Object -Last 5 -ExpandProperty message
