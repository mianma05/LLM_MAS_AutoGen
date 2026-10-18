(*
 * 电机控制程序（IEC 61131-3 结构化文本 ST）
 * 功能：
 *   1) 启动按钮 xStart 启动电机，停止按钮 xStop 停止电机（自保持）
 *   2) 急停 xEStop 为常闭安全联锁：断开即立即停机并锁存故障
 *   3) 启动前需满足安全条件，并延时确认（TON）后才允许输出
 *   4) 故障锁存后需复位按钮 xReset 且急停恢复闭合方可再启动
 *)

PROGRAM MotorControl
VAR
    (* 输入 *)
    xStart      : BOOL := FALSE;   (* 启动按钮，常开 *)
    xStop       : BOOL := FALSE;   (* 停止按钮，常开 *)
    xEStop      : BOOL := TRUE;    (* 急停，常闭：TRUE=安全回路闭合 *)
    xReset      : BOOL := FALSE;   (* 故障复位按钮，常开 *)

    (* 输出 *)
    xMotor      : BOOL := FALSE;   (* 电机接触器输出 *)
    xFaultLamp  : BOOL := FALSE;   (* 故障指示灯 *)

    (* 内部状态 *)
    xRunCmd     : BOOL := FALSE;   (* 运行指令（自保持） *)
    xFault      : BOOL := FALSE;   (* 故障锁存 *)
    xSafetyOK   : BOOL := FALSE;   (* 安全条件满足 *)

    (* 定时器 *)
    tonStartDelay : TON;           (* 启动确认延时 *)
END_VAR

(* ---------- 1. 安全联锁判断 ---------- *)
(* 急停常闭点闭合且无故障锁存，才视为安全条件满足 *)
xSafetyOK := xEStop AND NOT xFault;

(* ---------- 2. 故障锁存与复位 ---------- *)
IF NOT xEStop THEN
    (* 急停断开：立即锁存故障 *)
    xFault := TRUE;
ELSIF xReset AND xEStop THEN
    (* 急停已恢复且按下复位：清除故障 *)
    xFault := FALSE;
END_IF;

(* ---------- 3. 运行指令自保持（启动/停止） ---------- *)
IF xFault THEN
    (* 故障状态下强制撤销运行指令 *)
    xRunCmd := FALSE;
ELSIF xStart AND xSafetyOK THEN
    xRunCmd := TRUE;
ELSIF xStop THEN
    xRunCmd := FALSE;
END_IF;

(* ---------- 4. 启动确认延时（TON） ---------- *)
(* 运行指令有效且安全条件满足时开始计时，延时到才输出 *)
tonStartDelay(IN := xRunCmd AND xSafetyOK, PT := T#2S);

(* ---------- 5. 电机输出 ---------- *)
IF xFault THEN
    xMotor := FALSE;
ELSIF tonStartDelay.Q THEN
    xMotor := TRUE;
ELSE
    xMotor := FALSE;
END_IF;

(* ---------- 6. 故障指示 ---------- *)
xFaultLamp := xFault;

END_PROGRAM
