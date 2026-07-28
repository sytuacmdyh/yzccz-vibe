# Modbus Test

本上下文定义 CSV 驱动的 Modbus 测试会话中使用的核心术语，区分文件级结果与文件内步骤。

## Language

**Test File**:
一次独立判定结果的 CSV 测试场景，由按顺序执行的 Test Step 组成。
_Avoid_: Test Case、Test Item（用于指代整个 CSV 时）

**Test Step**:
Test File 中的一项测试操作。
_Avoid_: Test File、Test Case（用于指代单个步骤时）

**Simulator-dependent Test File**:
包含至少一个 DeviceSimulator 操作、因而依赖模拟器参与的 Test File。
_Avoid_: Simulator Test Step（用于指代整个文件时）

**Failed Test File**:
输入校验、解析或执行结果为失败的 Test File。
_Avoid_: Error Case

**Skipped Test File**:
因运行条件不满足而未执行，且不计为失败的有效 Test File。
_Avoid_: Failed Test File
