#Requires -Version 5.1
param(
  [string[]]$Csv = @("C:\Users\Tanta\Desktop\Nanhuaij_WorkSpace\hp-ctrl-box-gd32\tests\integration_test\mqtt_test\mqtt_debug.csv","C:\Users\Tanta\Desktop\Nanhuaij_WorkSpace\hp-ctrl-box-gd32\tests\integration_test\mqtt_test\time_sync\time_sync.csv","C:\Users\Tanta\Desktop\Nanhuaij_WorkSpace\hp-ctrl-box-gd32\tests\integration_test\mqtt_test\manual_wf\manual_workflow.csv","C:\Users\Tanta\Desktop\Nanhuaij_WorkSpace\hp-ctrl-box-gd32\tests\integration_test\mqtt_test\env_wf\env_change_workflow.csv"),
  [switch]$DryRun,
  [string]$Port,
  [string]$MqttConfig
)
$ErrorActionPreference = "Stop"
$skillRoot = $PSScriptRoot
$harnessPath = Join-Path $skillRoot "harness_config.json"
$cfg = Get-Content $harnessPath -Raw -Encoding UTF8 | ConvertFrom-Json
$serialPort = if ($Port) { $Port } else { $cfg.serial.port }
$baud = $cfg.serial.baudrate
$timeAddr = $cfg.serial.time_addr
$mqttApp = Join-Path $skillRoot "ems_mqtt_master\app_cli.py"
$defaultMqttCfg = "C:\Users\Tanta\Desktop\Nanhuaij_WorkSpace\hp-ctrl-box-gd32\tests\integration_test\mqtt_test\broker_config.json"
$mqttCfg = if ($MqttConfig) { $MqttConfig } else { $defaultMqttCfg }
$argsList = @()
$argsList += $Csv
$argsList += @("--port", $serialPort, "--baudrate", $baud, "--time-addr", $timeAddr)
$argsList += @("--mqtt-app", $mqttApp, "--mqtt-config", $mqttCfg)
if ($DryRun) { $argsList += "--dry-run" }
Write-Host "Harness: $harnessPath -> port=$serialPort baud=$baud time_addr=$timeAddr mqtt_config=$mqttCfg" -ForegroundColor Cyan
$script = Join-Path $skillRoot "scripts\modbus_test.py"
& python $script @argsList
