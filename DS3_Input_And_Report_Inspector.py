#!/usr/bin/env python3
import usb.core, usb.util
import sys
import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
import logging, time, binascii
import threading, re
import uuid
from collections import deque
from tkinter import filedialog
from typing import Optional, List, Tuple, Dict, Any, Deque, Union, Callable

logging.basicConfig(level=logging.INFO,
                    format='[%(levelname)s] %(module)s:%(lineno)d %(message)s')
logger = logging.getLogger(__name__)

VENDOR_ID: int = 0x054C
PRODUCT_ID: int = 0x0268

TETT = 0x04 | 0x08 | 0x10
logger.warning(f"DS3 Input & Report Inspector v1.00 (TETT={TETT})")

BM_REQUEST_TYPE_GET_DESCRIPTOR_STD_INTERFACE: int = 0x81
BM_REQUEST_TYPE_GET_FEATURE_HID_CLASS: int = 0xA1
BM_REQUEST_TYPE_SET_FEATURE_HID_CLASS: int = 0x21
BREQUEST_GET_DESCRIPTOR: int = 0x06
BREQUEST_GET_REPORT: int = 0x01
BREQUEST_SET_REPORT: int = 0x09
WVALUE_HIGH_FEATURE: int = (3 << 8)
WVALUE_HIGH_OUTPUT: int = (2 << 8)
WVALUE_HIGH_REPORT_DESC: int = (0x22 << 8)

ENABLE_OUTPUTS_SENSORS_PAYLOAD: bytes = bytes((0x42, 0x03))
ENABLE_INPUT_STREAMING_PAYLOAD: bytes = bytes((0x42, 0x02))
DISABLE_INPUT_STREAMING_PAYLOAD: bytes = bytes((0x42, 0x01))
RESTART_CONTROLLER_PAYLOAD: bytes = bytes((0x42, 0x04))
BT_PAIRING_REPORT_ID: int = 0xF5

_WIN_TIMEOUTS: set[int] = {110, 10060}

_PS3_REPORT_BUFFER_DEFAULT: bytearray = bytearray((
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xFF, 0x27, 0x10, 0x00, 0x32,
    0xFF, 0x27, 0x10, 0x00, 0x32,
    0xFF, 0x27, 0x10, 0x00, 0x32,
    0xFF, 0x27, 0x10, 0x00, 0x32,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
))

global_dev_handle: Optional[usb.core.Device] = None
global_intf_num: Optional[int] = None
endpoint_desc: Optional[usb.core.Endpoint] = None

polling_intervals: Deque[float] = deque(maxlen=100)
last_polling_timestamp: Optional[float] = None
current_polling_rate_hz: float = 0.0
latest_input_data: Dict[str, Optional[List[int]]] = {"data": None}
input_reports_enabled: bool = False
ps_button_warning_shown: bool = False
data_logging_enabled: bool = False

global_calibration_data: Dict[str, int] = {
    "acc_x_bias": 0, "acc_x_gain": 1024,
    "acc_y_bias": 0, "acc_y_gain": 1024,
    "acc_z_bias": 0, "acc_z_gain": 1024,
    "gyro_z_offset": 0,
    "fetched": False
}

STICK_CALIB_BANK: int = 0

DEFAULT_4PIN_CALIB_FLASH_START_ADDRESS: int = 0x0020
DEFAULT_4PIN_CALIB_DATA_TOTAL_LEN: int = 0x10
DEFAULT_4PIN_CALIB_NUM_MAIN_BYTES: int = 0x08

_3PIN_CALIB_FLASH_START_ADDRESS: int = 0x0046
_3PIN_CALIB_DATA_TOTAL_LEN: int = 0x10
_3PIN_CALIB_NUM_MAIN_BYTES: int = 0x10

FLASH_PAGE_SIZE_FOR_READ_CMD: int = 0x20

current_stick_calib_flash_address: int = DEFAULT_4PIN_CALIB_FLASH_START_ADDRESS
current_stick_calib_data_total_len: int = DEFAULT_4PIN_CALIB_DATA_TOTAL_LEN
current_stick_calib_num_main_bytes: int = DEFAULT_4PIN_CALIB_NUM_MAIN_BYTES
detected_stick_type: Optional[str] = None

global_stick_calib_bytes: Optional[bytearray] = None
global_stick_suffix_bytes: Optional[bytearray] = None
stick_calib_display_vars: List[tk.StringVar] = []
stick_calib_axis_value_vars: List[tk.StringVar] = []

_calib_write_pending_id: Optional[str] = None
_calib_write_lock = threading.Lock()

_repeat_job_id: Optional[str] = None
_repeating_byte_index: Optional[int] = None
_repeating_delta: Optional[int] = None
_initial_repeat_delay_ms: int = 400
_subsequent_repeat_delay_ms: int = 75

_adjust_press_time: Optional[float] = None
_adjust_hold_init_job_id: Optional[str] = None
_adjust_repeat_job_id: Optional[str] = None
_adjust_byte_index: Optional[int] = None
_adjust_delta: Optional[int] = None

CLICK_TIME_THRESHOLD_MS: int = 200
HOLD_FIRST_REPEAT_DELAY_MS: int = 200
HOLD_SUBSEQUENT_REPEAT_DELAY_MS: int = 75

root: Optional[tk.Tk] = None
polling_rate_label: Optional[ttk.Label] = None
connection_status_label: Optional[ttk.Label] = None
canvas_left: Optional[tk.Canvas] = None
canvas_right: Optional[tk.Canvas] = None
dot_left: Optional[int] = None
dot_right: Optional[int] = None
center: float = 0.0
radius: float = 0.0
button_vars: List[Tuple[tk.IntVar, int, bool]] = []
ps_var: Optional[tk.IntVar] = None
pressure_vars: Dict[str, tk.IntVar] = {}
sensor_vars: Dict[str, tk.StringVar] = {}
pressure_map: Dict[str, int] = {}
feature_text_area: Optional[scrolledtext.ScrolledText] = None
ui_controls_to_toggle: List[tk.Widget] = []
data_log_toggle_button: Optional[ttk.Button] = None
host_mac_entry_var: Optional[tk.StringVar] = None
current_paired_mac_var: Optional[tk.StringVar] = None
stick_calib_frame: Optional[ttk.LabelFrame] = None

RECONNECT_INTERVAL_MS: int = 5000
last_reconnect_attempt_time: float = 0.0

REPORT_ID_INPUT_ACTUAL: int = 0x01
INPUT_REPORT_BUTTONS_BYTE_OFFSET_0: int = 2
INPUT_REPORT_BUTTONS_BYTE_OFFSET_1: int = 3
INPUT_REPORT_PS_BUTTON_BYTE_OFFSET: int = 4
INPUT_REPORT_MIN_LENGTH_PS_BUTTON: int = 5
INPUT_REPORT_STICK_LX_OFFSET: int = 6
INPUT_REPORT_STICK_LY_OFFSET: int = 7
INPUT_REPORT_STICK_RX_OFFSET: int = 8
INPUT_REPORT_STICK_RY_OFFSET: int = 9
INPUT_REPORT_ACCEL_X_LOW_BYTE_OFFSET: int = 41
INPUT_REPORT_ACCEL_Y_LOW_BYTE_OFFSET: int = 43
INPUT_REPORT_ACCEL_Z_LOW_BYTE_OFFSET: int = 45
INPUT_REPORT_GYRO_Z_LOW_BYTE_OFFSET: int = 47

INPUT_REPORT_MIN_LENGTH_BUTTONS_PS: int = 5
INPUT_REPORT_MIN_LENGTH_STICKS: int = 10
INPUT_REPORT_MIN_LENGTH_SENSORS: int = 49

GRAVITY_MS2: float = 9.80665

ACCEL_SENSITIVITY_LSB_PER_G: float = 35583.0

GYRO_SENSITIVITY_LSB_PER_DPS: float = 14.31

GYRO_AXES: List[Tuple[str, str, str]] = [
    ("Pitch", "pitch_dps", "blue"),
    ("Yaw",   "yaw_dps",   "red"),
    ("Roll",  "roll_dps",  "orange")
]
ACCEL_AXES: List[Tuple[str, str, str]] = [
    ("X", "display_ax_ms2", "blue"),
    ("Y", "display_ay_ms2", "red"),
    ("Z", "display_az_ms2", "orange")
]

PHYSICAL_TO_TARGET_ACCEL_MAP: Dict[str, str] = {
    "script_x": "target_x",
    "script_y": "target_y",
    "script_z": "target_z"
}
PHYSICAL_TO_TARGET_ACCEL_INVERT: Dict[str, bool] = {
    "script_x": False,
    "script_y": False,
    "script_z": False
}

def toggle_data_logging() -> None:
    global data_logging_enabled, data_log_toggle_button
    data_logging_enabled = not data_logging_enabled

    if data_log_toggle_button:
        if data_logging_enabled:
            data_log_toggle_button.config(text="Disable Input Log")
            logger.info("Detailed update loop data logging ENABLED.")
        else:
            data_log_toggle_button.config(text="Enable Input Log")
            logger.info("Detailed update loop data logging DISABLED.")

def _dump_single_f1_bank(bank: int) -> bytes:
    if global_dev_handle is None or global_intf_num is None:
        raise RuntimeError("Device not initialised")

    out = bytearray()
    wValue = WVALUE_HIGH_FEATURE | 0xF1

    for page in range(0x00, 0x100, 0x10):
        sel = bytes([0x00, 0x0B, 0xFF, 0xFF,
                     bank & 0xFF, page & 0xFF,
                     0xFF, 0x10, 0xFF])
        com = bytearray(sel); com[7] = 0x10

        global_dev_handle.ctrl_transfer(
            BM_REQUEST_TYPE_SET_FEATURE_HID_CLASS, BREQUEST_SET_REPORT,
            wValue, global_intf_num, sel, timeout=200)

        raw = _get_raw_report_or_descriptor(
            BM_REQUEST_TYPE_GET_FEATURE_HID_CLASS, BREQUEST_GET_REPORT,
            wValue, global_intf_num, 64)

        if not raw or len(raw) < 37 or raw[0] != 0x57:
            out.extend(b'\x00' * 0x20)
        else:
            expected_len = raw[4] if len(raw) > 4 else 0
            if expected_len >= 0x10 and len(raw) >= 5 + 0x10:
                out.extend(raw[5 : 5 + 0x10])
            else:
                logger.warning(f"Dump F1: Received unexpected length ({expected_len}) for 16-byte read from 0x{page:02X}. Raw: {list(raw)}")
                out.extend(b'\x00' * 0x10)

    return bytes(out)

def _write_single_f1_bank(bank: int, data: bytes) -> None:
    if len(data) != 256:
        raise ValueError("bank data must be exactly 256 bytes")

    if global_dev_handle is None or global_intf_num is None:
        raise RuntimeError("Device not initialised")

    wValue = WVALUE_HIGH_FEATURE | 0xF1

    for address in range(0x00, 0x100, 0x10):
        chunk_data = data[address:address+0x10]
        payload = (bytes([0x00, 0x0A, 0xFF, 0xFF,
                          bank & 0xFF, address & 0xFF,
                          len(chunk_data)]) + chunk_data)
        global_dev_handle.ctrl_transfer(
            BM_REQUEST_TYPE_SET_FEATURE_HID_CLASS, BREQUEST_SET_REPORT,
            wValue, global_intf_num, payload, timeout=500)
        time.sleep(0.05)

def dump_f1_banks_to_file() -> None:
    if global_dev_handle is None or global_intf_num is None:
        messagebox.showerror("USB Error", "Controller not connected.", parent=root)
        return

    fname = filedialog.asksaveasfilename(
        defaultextension=".bin",
        filetypes=[("Raw binary", "*.bin"), ("All files", "*.*")],
        title="Save flash dump as…")
    if not fname:
        return

    try:
        bank_a = _dump_single_f1_bank(0)
        bank_b = _dump_single_f1_bank(1)
        with open(fname, "wb") as fh:
            fh.write(bank_a + bank_b)
        messagebox.showinfo("Flash dump saved", f"Wrote {fname}", parent=root)
    except Exception as e:
        messagebox.showerror("Error", f"Could not save dump:\n{e}", parent=root)

def flash_f1_banks_from_file() -> None:
    global input_reports_enabled
    
    if global_dev_handle is None or global_intf_num is None:
        messagebox.showerror("USB Error", "Controller not connected.", parent=root)
        return

    fname = filedialog.askopenfilename(
        filetypes=[("Raw binary", "*.bin"), ("All files", "*.*")],
        title="Select flash dump to write…")
    if not fname:
        return

    try:
        with open(fname, "rb") as fh:
            blob = fh.read()
        if len(blob) != 512:
            raise ValueError("file must be exactly 512 bytes (256 + 256)")

        was_polling = input_reports_enabled
        input_reports_enabled = False
        time.sleep(0.05)

        _write_single_f1_bank(0, blob[:256])
        _write_single_f1_bank(1, blob[256:])

        if was_polling:
            input_reports_enabled = True

        messagebox.showinfo("Flash written",
                            f"Successfully flashed both banks from {fname}",
                            parent=root)
    except Exception as e:
        messagebox.showerror("Error", f"Flashing failed:\n{e}", parent=root)

def _hex_dump_16(byte_block: bytes) -> str:
    return "\n".join(
        f"{ofs:04X}: " + " ".join(f"{b:02X}" for b in byte_block[ofs:ofs+16])
        for ofs in range(0, len(byte_block), 16)
    )

def dump_f1_banks_gui(title: str = "broken controller") -> None:
    global feature_text_area

    try:
        if global_dev_handle is None or global_intf_num is None:
            if not initialize_controller():
                messagebox.showerror("USB Error",
                                     "No DS3 controller connected.",
                                     parent=root)
                return

        bank_a = _dump_single_f1_bank(0)
        bank_b = _dump_single_f1_bank(1)

        ta = feature_text_area
        ta.configure(state='normal')
        ta.delete('1.0', tk.END)

        ta.insert(tk.END, f"Dump from {title}\n")
        ta.insert(tk.END, "--- 0xF1 FLASH DUMP --------------------------------\n")

        ta.insert(tk.END,
                  f"Bank A (block 0x00, {len(bank_a)} bytes)\n"
                  f"{_hex_dump_16(bank_a)}\n\n")

        ta.insert(tk.END,
                  f"Bank B (block 0x01, {len(bank_b)} bytes)\n"
                  f"{_hex_dump_16(bank_b)}\n")

        ta.configure(state='disabled')
        ta.see(tk.END)

    except usb.core.USBError as e:
        messagebox.showerror("USB Error", f"Failed to dump flash: {e}", parent=root)
    except Exception as ex:
        messagebox.showerror("Error", f"Unexpected error during dump:\n{ex}", parent=root)

def _read_16_byte_flash_chunk(bank: int, aligned_address: int) -> Optional[bytearray]:
    global global_dev_handle, global_intf_num
    wValue = WVALUE_HIGH_FEATURE | 0xF1
    read_length_from_device = 0x10

    sel_payload = bytes([0x00, 0x0B, 0xFF, 0xFF,
                         bank & 0xFF, aligned_address & 0xFF,
                         0xFF, read_length_from_device, 0xFF])
    commit_payload = bytearray(sel_payload); commit_payload[7] = 0x10

    try:
        logger.debug(f"RC: Sending SELECT 0xF1, 0x0B for addr 0x{aligned_address:02X}.")
        global_dev_handle.ctrl_transfer(BM_REQUEST_TYPE_SET_FEATURE_HID_CLASS, BREQUEST_SET_REPORT, wValue, global_intf_num, sel_payload, timeout=300)
        global_dev_handle.ctrl_transfer(BM_REQUEST_TYPE_SET_FEATURE_HID_CLASS, BREQUEST_SET_REPORT, wValue, global_intf_num, commit_payload, timeout=300)
        
        logger.debug(f"RC: Sending GET 0xF1 for addr 0x{aligned_address:02X}.")
        raw_report_data_array = _get_raw_report_or_descriptor(BM_REQUEST_TYPE_GET_FEATURE_HID_CLASS, BREQUEST_GET_REPORT, wValue, global_intf_num, 64)

        if not raw_report_data_array:
            logger.error(f"RC: FAILED to read 16-byte chunk from 0x{aligned_address:02X}. No data returned.")
            return None
            
        if raw_report_data_array[0] == 0x57:
            if len(raw_report_data_array) < (5 + read_length_from_device):
                logger.error(f"RC: FAILED to read 16-byte chunk from 0x{aligned_address:02X}. Raw response too short for 0x57 format: {list(raw_report_data_array)}")
                return None
            
            if raw_report_data_array[1] != 0x0B:
                logger.warning(f"RC: Unexpected subcommand 0x{raw_report_data_array[1]:02X} in 0x57 report from 0x{aligned_address:02X}.")

            expected_len_in_status_report = raw_report_data_array[4] if len(raw_report_data_array) > 4 else 0
            if expected_len_in_status_report != read_length_from_device:
                logger.warning(f"RC: Read 16-byte chunk from 0x{aligned_address:02X}. Expected data length 0x{read_length_from_device:02X}, got 0x{expected_len_in_status_report:02X} in 0x57 report. Raw: {list(raw_report_data_array)}")

            extracted_chunk = bytes(raw_report_data_array[5 : 5 + read_length_from_device])
        
        elif raw_report_data_array[0] == 0xF1:
            if len(raw_report_data_array) < (3 + read_length_from_device):
                logger.error(f"RC: FAILED to read 16-byte chunk from 0x{aligned_address:02X}. Raw response too short for 0xF1 format: {list(raw_report_data_array)}")
                return None
            
            if raw_report_data_array[1] != 0x0B:
                logger.error(f"RC: FAILED to read 16-byte chunk from 0x{aligned_address:02X}. Unexpected subcommand 0x{raw_report_data_array[1]:02X} in 0xF1 report. Raw: {list(raw_report_data_array)}")
                return None
            
            actual_data_len_in_response = raw_report_data_array[2]
            if actual_data_len_in_response != read_length_from_device:
                logger.warning(f"RC: Read 16-byte chunk from 0x{aligned_address:02X}. Expected data length 0x{read_length_from_device:02X}, got 0x{actual_data_len_in_response:02X} in 0xF1 report. Raw: {list(raw_report_data_array)}")

            extracted_chunk = bytes(raw_report_data_array[3 : 3 + read_length_from_device])
        
        else:
            logger.error(f"RC: FAILED to read 16-byte chunk from 0x{aligned_address:02X}. Unknown report format (first byte 0x{raw_report_data_array[0]:02X}). Raw: {list(raw_report_data_array)}")
            return None

        logger.debug(f"RC: SUCCESS read 16-byte chunk from 0x{aligned_address:02X}: {bytearray(extracted_chunk).hex()}")
        return bytearray(extracted_chunk)
    except usb.core.USBError as e:
        logger.error(f"RC: USBError reading 16-byte flash chunk from 0x{aligned_address:02X}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"RC: Unexpected error reading 16-byte flash chunk from 0x{aligned_address:02X}: {e}", exc_info=True)
        return None
    
def _write_16_byte_flash_chunk(bank: int, aligned_address: int, data_16_bytes: bytearray) -> bool:
    global global_dev_handle, global_intf_num
    if global_dev_handle is None or global_intf_num is None:
        logger.error("W16BC: Device not initialised for writing.")
        return False
    if len(data_16_bytes) != 0x10:
        logger.error(f"W16BC: Data chunk to write must be exactly 16 bytes, but got {len(data_16_bytes)}.")
        return False
    if aligned_address % 0x10 != 0:
        logger.error(f"W16BC: Aligned address 0x{aligned_address:02X} is not 16-byte aligned.")
        return False

    wValue = WVALUE_HIGH_FEATURE | 0xF1
    payload = (bytes([0x00, 0x0A, 0xFF, 0xFF,
                      bank & 0xFF, aligned_address & 0xFF,
                      0x10]) + data_16_bytes)

    try:
        logger.debug(f"W16BC: Sending WRITE 0xF1, 0x0A to Bank {bank}, Addr 0x{aligned_address:02X} (16 bytes): {data_16_bytes.hex()}")
        global_dev_handle.ctrl_transfer(
            BM_REQUEST_TYPE_SET_FEATURE_HID_CLASS, BREQUEST_SET_REPORT,
            wValue, global_intf_num, payload, timeout=500)
        time.sleep(0.05)
        return True
    except usb.core.USBError as e:
        logger.error(f"W16BC: USBError writing 16-byte flash chunk to 0x{aligned_address:02X}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"W16BC: Unexpected error writing 16-byte flash chunk to 0x{aligned_address:02X}: {e}", exc_info=True)
        return False
    
def u16_le_to_bytes(value: int) -> bytes:
    val = max(0, min(0xFFFF, int(value)))
    return bytes([val & 0xFF, (val >> 8) & 0xFF])

def fetch_stick_calibration_data() -> bool:
    global global_stick_calib_bytes, global_stick_suffix_bytes, stick_calib_display_vars
    global stick_calib_axis_value_vars, global_dev_handle, global_intf_num
    global current_stick_calib_flash_address, current_stick_calib_data_total_len, current_stick_calib_num_main_bytes
    global detected_stick_type

    logger.info("FSC: Starting fetch_stick_calibration_data.")
    if global_dev_handle is None or global_intf_num is None:
        logger.warning("FSC: Device not connected. Cannot fetch calibration data.")
        global_stick_calib_bytes = None
        global_stick_suffix_bytes = None
        if stick_calib_display_vars:
            for var in stick_calib_display_vars:
                if var: var.set("N/A")
        if stick_calib_axis_value_vars:
            for var in stick_calib_axis_value_vars:
                if var: var.set("----")
        return False

    try:
        start_calib_address = current_stick_calib_flash_address
        end_calib_address = start_calib_address + current_stick_calib_data_total_len - 1

        first_aligned_block_addr = (start_calib_address // 0x10) * 0x10
        last_aligned_block_addr = (end_calib_address // 0x10) * 0x10

        logger.info(f"FSC: Current stick type: {detected_stick_type}. Requested calib range: 0x{start_calib_address:02X}-0x{end_calib_address:02X} (Len: {current_stick_calib_data_total_len} / 0x{current_stick_calib_data_total_len:02X} bytes).")
        logger.info(f"FSC: Spanning flash blocks: 0x{first_aligned_block_addr:02X} to 0x{last_aligned_block_addr:02X}.")

        full_read_data = bytearray()
        
        block1 = _read_16_byte_flash_chunk(STICK_CALIB_BANK, first_aligned_block_addr)
        if block1 is None:
            logger.error(f"FSC: Failed to read first calibration block from 0x{first_aligned_block_addr:02X}. Setting UI to ERR_R and returning False.")
            if stick_calib_display_vars:
                for var in stick_calib_display_vars: var.set("ERR_R")
            if stick_calib_axis_value_vars:
                for var in stick_calib_axis_value_vars: var.set("ERR")
            return False
        full_read_data.extend(block1)
        logger.info(f"FSC: Successfully read block1 (0x{first_aligned_block_addr:02X}): {block1.hex()}")

        if first_aligned_block_addr != last_aligned_block_addr:
            time.sleep(0.02)
            block2 = _read_16_byte_flash_chunk(STICK_CALIB_BANK, last_aligned_block_addr)
            if block2 is None:
                logger.error(f"FSC: Failed to read second calibration block from 0x{last_aligned_block_addr:02X}. Setting UI to ERR_R and returning False.")
                if stick_calib_display_vars:
                    for var in stick_calib_display_vars: var.set("ERR_R")
                if stick_calib_axis_value_vars:
                    for var in stick_calib_axis_value_vars: var.set("ERR")
                return False
            full_read_data.extend(block2)
            logger.info(f"FSC: Successfully read block2 (0x{last_aligned_block_addr:02X}): {block2.hex()}")
            
        offset_in_full_read_data = start_calib_address - first_aligned_block_addr
        
        logger.info(f"FSC: Full combined raw data buffer (len {len(full_read_data)}): {full_read_data.hex()}")
        logger.info(f"FSC: Attempting slice from index {offset_in_full_read_data} with length {current_stick_calib_data_total_len}.")

        calib_segment_for_this_type = full_read_data[offset_in_full_read_data : offset_in_full_read_data + current_stick_calib_data_total_len]

        if len(calib_segment_for_this_type) != current_stick_calib_data_total_len:
            logger.error(f"FSC: CRITICAL MISMATCH! Extracted calib segment length ({len(calib_segment_for_this_type)}) "
                         f"does not match expected total length ({current_stick_calib_data_total_len}). "
                         f"This indicates a serious logic error in slicing or length definition. "
                         f"Requested start 0x{start_calib_address:02X}, expected len 0x{current_stick_calib_data_total_len:02X}. "
                         f"Full read data len: {len(full_read_data)}. Slicing parameters: {offset_in_full_read_data}:{offset_in_full_read_data + current_stick_calib_data_total_len}.")
            global_stick_calib_bytes = None; global_stick_suffix_bytes = None
            if stick_calib_display_vars:
                for var in stick_calib_display_vars: var.set("LOGIC_ERR_R")
            if stick_calib_axis_value_vars:
                for var in stick_calib_axis_value_vars: var.set("LOGIC_ERR")
            return False

        logger.info(f"FSC: Extracted calibration segment (len {len(calib_segment_for_this_type)} / 0x{len(calib_segment_for_this_type):02X}): {calib_segment_for_this_type.hex()}")

        global_stick_calib_bytes = bytearray(calib_segment_for_this_type[:current_stick_calib_num_main_bytes])
        global_stick_suffix_bytes = bytearray(calib_segment_for_this_type[current_stick_calib_num_main_bytes:])

        logger.info(f"FSC: Parsed main calib bytes: {global_stick_calib_bytes.hex()}")
        logger.info(f"FSC: Parsed suffix bytes: {global_stick_suffix_bytes.hex()}")

        full_bytes_for_display = list(global_stick_calib_bytes) + list(global_stick_suffix_bytes)
        if root and root.winfo_exists():
            root.after(0, lambda: _update_stick_calib_display_vars_from_bytes(full_bytes_for_display))
            root.after(0, update_16bit_axis_value_display_from_bytes)

        logger.info(f"FSC: Successfully fetched and parsed stick calib data. Check UI.")
        return True

    except usb.core.USBError as e:
        logger.error(f"FSC: USBError during fetch_stick_calibration_data: {e}", exc_info=True)
        global_stick_calib_bytes = None; global_stick_suffix_bytes = None
        if stick_calib_display_vars:
            if root and root.winfo_exists():
                root.after(0, lambda: [var.set("USB_FAIL_R") for var in stick_calib_display_vars if var])
        if stick_calib_axis_value_vars:
            if root and root.winfo_exists():
                root.after(0, lambda: [var.set("USB_FAIL") for var in stick_calib_axis_value_vars if var])
    except Exception as e:
        logger.error(f"FSC: Unexpected error during fetch_stick_calibration_data: {e}", exc_info=True)
        global_stick_calib_bytes = None; global_stick_suffix_bytes = None
        if stick_calib_display_vars:
            if root and root.winfo_exists():
                root.after(0, lambda: [var.set("GEN_FAIL_R") for var in stick_calib_display_vars if var])
        if stick_calib_axis_value_vars:
            if root and root.winfo_exists():
                root.after(0, lambda: [var.set("GEN_FAIL") for var in stick_calib_axis_value_vars if var])
    return False

def _update_stick_calib_display_vars_from_bytes(bytes_for_display: List[int]) -> None:
    global stick_calib_display_vars
    for i in range(min(len(bytes_for_display), len(stick_calib_display_vars))):
        if stick_calib_display_vars[i]:
            stick_calib_display_vars[i].set(f"{bytes_for_display[i]:02X}")
    for i in range(len(bytes_for_display), len(stick_calib_display_vars)):
        if stick_calib_display_vars[i]:
            stick_calib_display_vars[i].set("N/A")

def write_stick_calibration_data() -> bool:
    global global_stick_calib_bytes, global_stick_suffix_bytes
    global global_dev_handle, global_intf_num
    global current_stick_calib_flash_address, current_stick_calib_data_total_len

    if global_dev_handle is None or global_intf_num is None:
        logger.error("write_stick_calibration_data: Device not connected.")
        return False
    if global_stick_calib_bytes is None or global_stick_suffix_bytes is None:
        logger.error("write_stick_calibration_data: Calibration data not loaded/initialized in memory.")
        return False
    
    data_to_write_full_block = global_stick_calib_bytes + global_stick_suffix_bytes
    if len(data_to_write_full_block) != current_stick_calib_data_total_len:
        logger.error(f"WSCD: Data to write length ({len(data_to_write_full_block)}) does not match current stick type's expected length ({current_stick_calib_data_total_len}). Aborting write.")
        return False

    try:
        full_calib_segment_to_write = bytearray(global_stick_calib_bytes + global_stick_suffix_bytes)

        if len(full_calib_segment_to_write) != current_stick_calib_data_total_len:
            logger.error(f"WSCD: Data to write length ({len(full_calib_segment_to_write)}) does not match current stick type's expected length ({current_stick_calib_data_total_len}). Aborting write.")
            return False

        start_addr = current_stick_calib_flash_address
        end_addr = start_addr + current_stick_calib_data_total_len - 1

        first_page_addr = (start_addr // 0x10) * 0x10
        last_page_addr = (end_addr // 0x10) * 0x10

        logger.info(f"WSCD: Preparing to write calib data starting at 0x{start_addr:02X}, length {current_stick_calib_data_total_len} bytes.")
        logger.info(f"WSCD: This spans flash pages 0x{first_page_addr:02X} to 0x{last_page_addr:02X}.")

        page1_original = _read_16_byte_flash_chunk(STICK_CALIB_BANK, first_page_addr)
        if page1_original is None:
            logger.error(f"WSCD: Failed to read page 0x{first_page_addr:02X} before writing.")
            return False
        page1_modified = bytearray(page1_original)

        offset_in_page1 = start_addr - first_page_addr
        bytes_to_copy_to_page1 = min(0x10 - offset_in_page1, len(full_calib_segment_to_write))

        logger.debug(f"WSCD: Copying {bytes_to_copy_to_page1} bytes to page 1 (0x{first_page_addr:02X}) at offset {offset_in_page1}.")
        page1_modified[offset_in_page1 : offset_in_page1 + bytes_to_copy_to_page1] = \
            full_calib_segment_to_write[0 : bytes_to_copy_to_page1]

        if not _write_16_byte_flash_chunk(STICK_CALIB_BANK, first_page_addr, page1_modified):
            logger.error(f"WSCD: Failed to write modified page 0x{first_page_addr:02X}.")
            return False
        
        if first_page_addr != last_page_addr:
            time.sleep(0.02)
            page2_original = _read_16_byte_flash_chunk(STICK_CALIB_BANK, last_page_addr)
            if page2_original is None:
                logger.error(f"WSCD: Failed to read page 0x{last_page_addr:02X} before writing.")
                return False
            page2_modified = bytearray(page2_original)

            bytes_copied_to_page1 = bytes_to_copy_to_page1
            bytes_remaining = len(full_calib_segment_to_write) - bytes_copied_to_page1
            
            offset_in_page2 = 0 

            if bytes_remaining > 0:
                logger.debug(f"WSCD: Copying {bytes_remaining} bytes to page 2 (0x{last_page_addr:02X}) at offset {offset_in_page2}.")
                page2_modified[offset_in_page2 : offset_in_page2 + bytes_remaining] = \
                    full_calib_segment_to_write[bytes_copied_to_page1 : bytes_copied_to_page1 + bytes_remaining]
                
                if not _write_16_byte_flash_chunk(STICK_CALIB_BANK, last_page_addr, page2_modified):
                    logger.error(f"WSCD: Failed to write modified page 0x{last_page_addr:02X}.")
                    return False
            else:
                logger.debug("WSCD: No remaining bytes to copy to second page (this should happen if calib fits entirely in first page).")

        logger.info(f"WSCD: Successfully wrote stick calibration data to flash.")
        return True

    except usb.core.USBError as e:
        logger.error(f"WSCD: USBError writing stick calibration data: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"WSCD: Unexpected error writing stick calibration data: {e}", exc_info=True)
    return False

def _schedule_calib_write_debounce() -> None:
    global _calib_write_pending_id, root

    if root is None or not root.winfo_exists():
        logger.warning("Root window not available, cannot schedule calibration write.")
        return

    if _calib_write_pending_id:
        try:
            root.after_cancel(_calib_write_pending_id)
            logger.debug("Cancelled pending calibration write.")
        except ValueError:
            logger.debug("Could not cancel pending calibration write (might have already run or invalid ID).")
        _calib_write_pending_id = None

    _calib_write_pending_id = root.after(1000, _perform_calib_write_in_thread)
    logger.debug("Scheduled new calibration write for 1 second from now.")

def _perform_calib_write_in_thread() -> None:
    global _calib_write_pending_id
    _calib_write_pending_id = None

    if not _calib_write_lock.acquire(blocking=False):
        logger.warning("Calibration write already in progress or lock contention. Skipping this write.")
        return

    logger.info("Debounced calibration write triggered. Starting background thread for USB operation.")
    thread = threading.Thread(target=_do_calib_write_and_refetch, daemon=True)
    thread.start()

def _do_calib_write_and_refetch() -> None:
    global _calib_write_lock, root

    try:
        success = write_stick_calibration_data()
        if not success:
            logger.error("Background calib write: write_stick_calibration_data failed.")
            if root and root.winfo_exists():
                root.after(0, lambda: messagebox.showerror("Calibration Write Error", "Failed to write calibration data to controller.", parent=root))
            return

        logger.info("Background calib write: Successfully wrote data. Now fetching actual state from controller.")
        fetch_stick_calibration_data()

    except Exception as e:
        logger.critical(f"Unhandled exception in background calib write thread: {e}", exc_info=True)
        if root and root.winfo_exists():
            root.after(0, lambda: messagebox.showerror("Critical Calibration Error", f"An unexpected error occurred during calibration write:\n{e}", parent=root))
    finally:
        _calib_write_lock.release()
        logger.info("Calibration write thread finished and lock released.")

def adjust_stick_calib_value_16bit(axis_index: int, delta: int) -> None:
    global global_stick_calib_bytes, stick_calib_axis_value_vars, stick_calib_display_vars, root, current_stick_calib_num_main_bytes

    if global_stick_calib_bytes is None:
        messagebox.showerror("Calibration Error", "Calibration data not fetched yet. Connect controller and fetch data first.", parent=root)
        return

    if not (0 <= axis_index < 4):
        logger.error(f"adjust_stick_calib_value_16bit: Invalid axis_index {axis_index}")
        return

    start_byte_offset = axis_index * 2
    
    if not (start_byte_offset + 1 < current_stick_calib_num_main_bytes):
        logger.error(f"adjust_stick_calib_value_16bit: Axis {axis_index} (bytes {start_byte_offset}-{start_byte_offset+1}) is outside current_stick_calib_num_main_bytes ({current_stick_calib_num_main_bytes}). Adjustment prevented.")
        messagebox.showerror("Calibration Error", "Cannot adjust this 16-bit value. It falls outside the adjustable range for the detected stick type.", parent=root)
        return

    current_val_16bit = u16_le(global_stick_calib_bytes, start_byte_offset)

    new_val_16bit = current_val_16bit + delta
    new_val_16bit = max(0, min(0xFFFF, new_val_16bit))

    new_bytes_for_axis = u16_le_to_bytes(new_val_16bit)
    global_stick_calib_bytes[start_byte_offset] = new_bytes_for_axis[0]
    global_stick_calib_bytes[start_byte_offset + 1] = new_bytes_for_axis[1]

    logger.info(f"Stick calib axis {axis_index} in-memory adjusted to {new_val_16bit:04X}.")
    if axis_index < len(stick_calib_axis_value_vars) and stick_calib_axis_value_vars[axis_index]:
        stick_calib_axis_value_vars[axis_index].set(f"{new_val_16bit:04X}")
    if start_byte_offset < len(stick_calib_display_vars) and stick_calib_display_vars[start_byte_offset]:
        stick_calib_display_vars[start_byte_offset].set(f"{new_bytes_for_axis[0]:02X}")
    if start_byte_offset + 1 < len(stick_calib_display_vars) and stick_calib_display_vars[start_byte_offset + 1]:
        stick_calib_display_vars[start_byte_offset + 1].set(f"{new_bytes_for_axis[1]:02X}")
    
    _schedule_calib_write_debounce()
           
def _update_stick_calib_ui_elements():
    global stick_calib_frame, ui_controls_to_toggle
    global stick_calib_display_vars, stick_calib_axis_value_vars
    global current_stick_calib_num_main_bytes, current_stick_calib_data_total_len
    global detected_stick_type

    if not (stick_calib_frame and stick_calib_frame.winfo_exists()):
        logger.warning("_update_stick_calib_ui_elements: Stick calibration frame not available.")
        return

    old_calib_controls_to_remove = []
    for child in stick_calib_frame.winfo_children():
        if child in ui_controls_to_toggle:
            old_calib_controls_to_remove.append(child)
        child.destroy()
    
    for old_control in old_calib_controls_to_remove:
        if old_control in ui_controls_to_toggle:
            ui_controls_to_toggle.remove(old_control)

    start_byte_label = 0
    end_byte_label = current_stick_calib_num_main_bytes - 1
    stick_calib_frame.config(text=f"Stick Center Calibration (Bytes {start_byte_label}-{end_byte_label})")

    stick_calib_display_vars = [tk.StringVar(value="N/A") for _ in range(DEFAULT_4PIN_CALIB_DATA_TOTAL_LEN)]
    stick_calib_axis_value_vars = [tk.StringVar(value="----") for _ in range(4)]

    current_row_offset = 0

    byte_control_labels_template = [
        "Byte 0 (LX LSB):", "Byte 1 (LX MSB):",
        "Byte 2 (LY LSB):", "Byte 3 (LY MSB):",
        "Byte 4 (RX LSB):", "Byte 5 (RX MSB):",
        "Byte 6 (RY LSB):", "Byte 7 (RY MSB):"
    ]
    
    if detected_stick_type == "3-pin":
        byte_control_labels_template = [
            "Byte 0 (LX LSB):", "Byte 1 (LX MSB):",
            "Byte 2 (LX LSB):", "Byte 3 (LX MSB):",
            "Byte 4 (LY LSB):", "Byte 5 (LY MSB):",
            "Byte 6 (LY LSB):", "Byte 7 (LY MSB):",
            "Byte 8 (RX LSB):", "Byte 9 (RX MSB):",
            "Byte 10 (RX LSB):", "Byte 11 (RX MSB):",
            "Byte 12 (RY LSB):", "Byte 13 (RY MSB):",
            "Byte 14 (RY LSB):", "Byte 15 (RY MSB):"
        ]

    for i in range(current_stick_calib_num_main_bytes):
        label_text = byte_control_labels_template[i] if i < len(byte_control_labels_template) else f"Byte {i}:"
        
        ttk.Label(stick_calib_frame, text=label_text).grid(row=current_row_offset + i, column=0, sticky="w", padx=2, pady=3)
        
        byte_val_display = ttk.Label(stick_calib_frame, textvariable=stick_calib_display_vars[i], width=4, anchor="center")
        byte_val_display.grid(row=current_row_offset + i, column=1, sticky="ew", padx=2)
        
        minus_btn = ttk.Button(stick_calib_frame, text="-", width=3)
        minus_btn.grid(row=current_row_offset + i, column=2, padx=(5,1))
        minus_btn.bind("<ButtonPress-1>", lambda event, b_idx=i: handle_button_press_for_adjust(event, b_idx, -1))
        minus_btn.bind("<ButtonRelease-1>", handle_button_release_for_adjust)
        ui_controls_to_toggle.append(minus_btn)

        plus_btn = ttk.Button(stick_calib_frame, text="+", width=3)
        plus_btn.grid(row=current_row_offset + i, column=3, padx=(1,5))
        plus_btn.bind("<ButtonPress-1>", lambda event, b_idx=i: handle_button_press_for_adjust(event, b_idx, +1))
        plus_btn.bind("<ButtonRelease-1>", handle_button_release_for_adjust)
        ui_controls_to_toggle.append(plus_btn)

    current_row_offset += current_stick_calib_num_main_bytes

    if detected_stick_type == "3-pin":
        ttk.Separator(stick_calib_frame, orient=tk.HORIZONTAL).grid(row=current_row_offset, column=0, columnspan=4, sticky="ew", pady=(10,5))
        current_row_offset += 1
        info_label_3pin = ttk.Label(
            stick_calib_frame,
            text="For 3-pin sticks, controller restart is required for calibration changes to take effect.",
            foreground="red",
            wraplength=stick_calib_frame.winfo_width() - 20
        )
        info_label_3pin.grid(row=current_row_offset, column=0, columnspan=4, sticky="ew", padx=5, pady=(5,10))
        current_row_offset += 1

    stick_calib_frame.columnconfigure(1, weight=1)

    fetch_stick_calibration_data()

def adjust_stick_calib_byte(byte_index_in_calib: int, delta: int) -> None:
    global global_stick_calib_bytes, stick_calib_display_vars, root, current_stick_calib_num_main_bytes

    if global_stick_calib_bytes is None:
        messagebox.showerror("Calibration Error", "Calibration data not fetched yet. Connect controller and fetch data first.", parent=root)
        return

    if not (0 <= byte_index_in_calib < current_stick_calib_num_main_bytes):
        logger.error(f"adjust_stick_calib_byte: Invalid byte_index {byte_index_in_calib} for current_stick_calib_num_main_bytes ({current_stick_calib_num_main_bytes}). Adjustment prevented.")
        messagebox.showerror("Calibration Error", "Cannot adjust this byte. It falls outside the adjustable range for the detected stick type.", parent=root)
        return

    current_val = global_stick_calib_bytes[byte_index_in_calib]
    new_val = current_val + delta
    new_val = max(0, min(255, new_val))

    global_stick_calib_bytes[byte_index_in_calib] = new_val

    logger.info(f"Stick calib byte {byte_index_in_calib} in-memory adjusted to {new_val:02X}.")
    if byte_index_in_calib < len(stick_calib_display_vars) and stick_calib_display_vars[byte_index_in_calib]:
        stick_calib_display_vars[byte_index_in_calib].set(f"{new_val:02X}")
    
    update_16bit_axis_value_display_from_bytes()
    
    _schedule_calib_write_debounce()

def update_16bit_axis_value_display_from_bytes():
    global global_stick_calib_bytes, stick_calib_axis_value_vars

    if global_stick_calib_bytes is None:
        for var in stick_calib_axis_value_vars:
            if var: var.set("ERR_B")
        return

    axis_byte_start_indices = [0, 2, 4, 6]

    for i in range(len(stick_calib_axis_value_vars)):
        if i < len(stick_calib_axis_value_vars) and stick_calib_axis_value_vars[i]:
            start_byte_idx = axis_byte_start_indices[i]
            if len(global_stick_calib_bytes) >= start_byte_idx + 2:
                val_16bit = u16_le(global_stick_calib_bytes, start_byte_idx)
                stick_calib_axis_value_vars[i].set(f"{val_16bit:04X}")
            else:
                stick_calib_axis_value_vars[i].set("ERR_S")

def determine_stick_type_and_update_ui():
    global connection_status_label, global_stick_calib_bytes, detected_stick_type
    global current_stick_calib_flash_address, current_stick_calib_data_total_len, current_stick_calib_num_main_bytes
    
    stick_type_suffix = " (Unknown PIN type)"

    if global_stick_calib_bytes is not None and len(global_stick_calib_bytes) >= 5:
        first_5_bytes_at_0x20 = global_stick_calib_bytes[0:5]
        
        if first_5_bytes_at_0x20 == bytearray([0x00, 0x00, 0x00, 0x00, 0x00]):
            detected_stick_type = "3-pin"
            stick_type_suffix = " (3 PIN)"
            logger.info("Determined stick type: 3-pin analog stick (based on 0x20-0x24 being all zeros).")
            
            current_stick_calib_flash_address = _3PIN_CALIB_FLASH_START_ADDRESS
            current_stick_calib_data_total_len = _3PIN_CALIB_DATA_TOTAL_LEN
            current_stick_calib_num_main_bytes = _3PIN_CALIB_NUM_MAIN_BYTES
            
        else:
            detected_stick_type = "4-pin"
            stick_type_suffix = " (4 PIN)"
            logger.info(f"Determined stick type: 4-pin analog stick (based on 0x20-0x24 being non-zero: {first_5_bytes_at_0x20.hex()}).")
            
            current_stick_calib_flash_address = DEFAULT_4PIN_CALIB_FLASH_START_ADDRESS
            current_stick_calib_data_total_len = DEFAULT_4PIN_CALIB_DATA_TOTAL_LEN
            current_stick_calib_num_main_bytes = DEFAULT_4PIN_CALIB_NUM_MAIN_BYTES
            
    else:
        detected_stick_type = None
        logger.warning("Could not determine stick type: Initial calibration data (0x20 block) not available or too short for type detection.")

    if root and root.winfo_exists():
        root.after(0, _update_stick_calib_ui_elements)
        
    if connection_status_label and connection_status_label.winfo_exists():
        current_text = connection_status_label.cget("text")
        if "Connected to DS3" in current_text or "Disconnected" in current_text:
             connection_status_label.config(text=f"Connected to DS3{stick_type_suffix}")
        else:
            connection_status_label.config(text=f"Connected to DS3{stick_type_suffix}")

def get_host_bt_mac_address() -> str:
    host_mac_str: Optional[str] = None
    
    try:
        mac_int = uuid.getnode()
        if mac_int != 0:
            hex_mac = hex(mac_int)[2:].zfill(12)
            if len(hex_mac) == 12:
                host_mac_str = hex_mac
                logger.info(f"Host MAC via uuid.getnode(): {host_mac_str} (Please verify this is your Bluetooth adapter's MAC)")
            else:
                logger.warning(f"uuid.getnode() returned an unexpected format: {hex_mac} (from int: {mac_int})")
                host_mac_str = None
    except Exception as e_uuid:
        logger.warning(f"Error getting MAC via uuid.getnode(): {e_uuid}")
        host_mac_str = None
            
    if host_mac_str:
        cleaned_mac = re.sub(r'[^0-9a-fA-F]', '', host_mac_str)
        if len(cleaned_mac) == 12:
            formatted_mac = " ".join(cleaned_mac[i:i+2] for i in range(0, 12, 2)).upper()
            logger.info(f"Using detected/formatted MAC: {formatted_mac}")
            return formatted_mac
        else:
            logger.warning(f"Detected MAC string '{host_mac_str}' (cleaned: '{cleaned_mac}') is not a valid 6-byte MAC. Falling back to placeholder.")
            host_mac_str = None

    placeholder_mac_value = "00 00 00 00 00 00"
    logger.info(f"Could not reliably auto-detect a valid host Bluetooth MAC. Using placeholder: {placeholder_mac_value}. "
                "Please MANUALLY VERIFY this is your ACTUAL Bluetooth adapter MAC address.")
    return placeholder_mac_value

def fetch_and_display_current_paired_mac() -> None:
    global global_dev_handle, global_intf_num, current_paired_mac_var, feature_text_area
    global global_stick_calib_bytes, global_stick_suffix_bytes
    global stick_calib_display_vars, stick_calib_axis_value_vars

    if not current_paired_mac_var:
        logger.warning("fetch_and_display_current_paired_mac: UI variable not ready.")
        return

    if global_dev_handle is None or global_intf_num is None:
        current_paired_mac_var.set("N/A (Disconnected)")
        return

    logger.info("Fetching current paired MAC from controller (GET Report 0xF5)...")
    
    length_to_request_f5_get = 17

    raw_data = _get_raw_report_or_descriptor(
        BM_REQUEST_TYPE_GET_FEATURE_HID_CLASS, BREQUEST_GET_REPORT,
        WVALUE_HIGH_FEATURE | BT_PAIRING_REPORT_ID,
        global_intf_num,
        length_to_request_f5_get
    )

    if raw_data:
        data_list = list(raw_data)
        mac_offset_in_get_f5 = 2
        
        if len(data_list) >= mac_offset_in_get_f5 + 6:
            fetched_mac_str = parse_mac_address(data_list, mac_offset_in_get_f5, length=6, reverse=False)
            
            if fetched_mac_str != "N/A":
                formatted_mac = fetched_mac_str.upper().replace(":", " ")
                current_paired_mac_var.set(formatted_mac)
                logger.info(f"Controller reports current paired MAC (0xF5 GET): {formatted_mac}")
            else:
                current_paired_mac_var.set("Error parsing MAC")
                logger.warning(f"Could not parse MAC from GET Report 0xF5. Raw: {data_list}")
        else:
            current_paired_mac_var.set("Report 0xF5 too short")
            logger.warning(f"GET Report 0xF5 response too short for MAC. Len: {len(data_list)}. Raw: {data_list}")
            if feature_text_area and feature_text_area.winfo_exists():
                feature_text_area.configure(state='normal')
                feature_text_area.insert(tk.END, f"GET Report 0xF5 FAILED or data too short. Raw: {list(raw_data)}\n")
                feature_text_area.see(tk.END)
                feature_text_area.configure(state='disabled')
    else:
        current_paired_mac_var.set("Failed to GET 0xF5")
        logger.error("Failed to retrieve GET Report 0xF5 from controller (no data returned).")
        if feature_text_area and feature_text_area.winfo_exists():
            feature_text_area.configure(state='normal')
            feature_text_area.insert(tk.END, "GET Report 0xF5 FAILED (no data returned).\n")
            feature_text_area.see(tk.END)
            feature_text_area.configure(state='disabled')

def send_bt_pair_request_cmd() -> None:
    global input_reports_enabled

    if global_dev_handle is None or global_intf_num is None:
        messagebox.showerror("Error", "Device not initialized. Cannot send pair request.", parent=root)
        return
    if not feature_text_area or not host_mac_entry_var:
        logger.error("UI elements for BT pairing not available.")
        return

    mac_str = host_mac_entry_var.get()
    if not mac_str:
        messagebox.showerror("Error", "Host BT MAC address cannot be empty.", parent=root)
        return

    cleaned_mac_str = re.sub(r'[^0-9a-fA-F]', '', mac_str)
    if len(cleaned_mac_str) != 12:
        messagebox.showerror("Error", "Invalid MAC address format. Must be 6 pairs of hex digits (e.g., AA:BB:CC:DD:EE:FF).", parent=root)
        return

    try:
        mac_bytes = bytes.fromhex(cleaned_mac_str)
    except ValueError:
        messagebox.showerror("Error", "Invalid hex characters in MAC address.", parent=root)
        return

    reversed_mac_data = mac_bytes

    full_payload_for_f5 = bytes([0x00, 0x00]) + reversed_mac_data 

    logger.info(f"Attempting to send BT Pair Request (Report ID 0x{BT_PAIRING_REPORT_ID:02X})")
    logger.info(f"  Original MAC: {mac_str}")
    logger.info(f"  Parsed MAC bytes: {[f'0x{b:02X}' for b in mac_bytes]}")
    logger.info(f"  Reversed MAC data: {[f'0x{b:02X}' for b in reversed_mac_data]}")
    logger.info(f"  Full Payload (ID + MAC): {[f'0x{b:02X}' for b in full_payload_for_f5]}")
    feature_text_area.configure(state='normal')
    feature_text_area.insert(tk.END, f"--- Sending Bluetooth Pair Request (Feature Report 0x{BT_PAIRING_REPORT_ID:02X}) ---\n")
    feature_text_area.insert(tk.END, f"  Host MAC (as entered): {mac_str}\n")
    feature_text_area.insert(tk.END, f"  Payload (reversed MAC): {[f'0x{b:02X}' for b in reversed_mac_data]}\n")

    try:
        wValue = WVALUE_HIGH_FEATURE | BT_PAIRING_REPORT_ID
        bytes_written = global_dev_handle.ctrl_transfer(
            BM_REQUEST_TYPE_SET_FEATURE_HID_CLASS, BREQUEST_SET_REPORT,
            wValue, global_intf_num,
            full_payload_for_f5, 
            timeout=1500
        )
        msg = f"BT Pair Request (0x{BT_PAIRING_REPORT_ID:02X}) sent successfully. Bytes written: {bytes_written} (Expected {len(full_payload_for_f5)})\n"
        msg += "  Controller should now be paired with the host MAC address provided.\n"
        msg += "  You may need to disconnect USB and press PS button to connect via Bluetooth.\n"
        
        if feature_text_area.winfo_exists():
            feature_text_area.configure(state='normal')
            feature_text_area.insert(tk.END, msg)
        
        messagebox.showinfo("Pairing Request Sent", "Bluetooth pairing command sent to controller. Disconnect USB and press PS button to test.", parent=root)

        cmd_disable_input_reports()
        restart_controller_cmd()
        input_reports_enabled = True

        if root and root.winfo_exists():
            logger.info("Scheduling MAC re-fetch in 1 second to verify pairing.")
            root.after(1000, fetch_and_display_current_paired_mac)

    except usb.core.USBError as e:
        msg = f"BT Pair Request (0x{BT_PAIRING_REPORT_ID:02X}) FAILED: {e}\n"
        feature_text_area.insert(tk.END, msg)
        messagebox.showerror("USB Error", f"Failed to send pairing request: {e}", parent=root)
    except Exception as ex:
        msg = f"BT Pair Request (0x{BT_PAIRING_REPORT_ID:02X}) General Error: {ex}\n"
        feature_text_area.insert(tk.END, msg)
        messagebox.showerror("Error", f"An unexpected error occurred: {ex}", parent=root)
    finally:
        feature_text_area.see(tk.END)
        feature_text_area.configure(state='disabled')

def parse_mac_address(byte_array: Union[bytes, bytearray, List[int]], offset: int, length: int = 6, reverse: bool = True) -> str:
    if not byte_array or offset + length > len(byte_array):
        return "N/A"
    mac_bytes = byte_array[offset : offset + length]
    if reverse:
        mac_bytes = mac_bytes[::-1]
    return ":".join(f"{b:02X}" for b in mac_bytes)

def s16_le(data: Union[bytes, bytearray, List[int]], offset: int) -> int:
    if not data or offset + 1 >= len(data):
        return 0
    val = data[offset] | (data[offset+1] << 8)
    return val - 0x10000 if val & 0x8000 else val

def u16_le(data: Union[bytes, bytearray, List[int]], offset: int) -> int:
    if not data or offset + 1 >= len(data):
        return 0
    return data[offset] | (data[offset+1] << 8)

def u32_le(data: Union[bytes, bytearray, List[int]], offset: int) -> int:
    if not data or offset + 3 >= len(data):
        return 0
    return (data[offset] | (data[offset+1] << 8)
            | (data[offset+2] << 16) | (data[offset+3] << 24))

def _sony_set_operational(dev: usb.core.Device, ifnum: int) -> bool:
    logger.info("Attempting GET_REPORT Feature 0xF2 (wake-up command)...")
    try:
        wValue = WVALUE_HIGH_FEATURE | 0xF2
        response = dev.ctrl_transfer(
            BM_REQUEST_TYPE_GET_FEATURE_HID_CLASS, BREQUEST_GET_REPORT,
            wValue, ifnum, 17, timeout=1000)
        logger.info(f"GET_REPORT 0xF2 OK (len {len(response)})")
        return True
    except usb.core.USBError as e:
        logger.error(f"GET_REPORT 0xF2 FAILED: {e}")
        return False

def _enable_outputs_and_sensors(dev: usb.core.Device, ifnum: int) -> bool:
    logger.info("Attempting SET_REPORT Feature 0xF4 (enable general outputs/sensors)...")
    try:
        wValue = WVALUE_HIGH_FEATURE | 0xF4
        bytes_written = dev.ctrl_transfer(
            BM_REQUEST_TYPE_SET_FEATURE_HID_CLASS, BREQUEST_SET_REPORT,
            wValue, ifnum, ENABLE_OUTPUTS_SENSORS_PAYLOAD, timeout=1000)
        logger.info(f"SET_REPORT 0xF4 (Payload {list(ENABLE_OUTPUTS_SENSORS_PAYLOAD)}) OK ({bytes_written} bytes)")
        return True
    except usb.core.USBError as e:
        logger.error(f"SET_REPORT 0xF4 (Payload {list(ENABLE_OUTPUTS_SENSORS_PAYLOAD)}) FAILED: {e}")
        return False

def set_led_and_rumble(led_pattern: int,
                       right_motor_duration: int, right_motor_strength: int,
                       left_motor_duration: int,  left_motor_strength:  int) -> None:
    if global_dev_handle is None or global_intf_num is None:
        logger.error("Device not initialised for LED/Rumble")
        return

    payload = bytearray(_PS3_REPORT_BUFFER_DEFAULT)
    payload[1] = right_motor_duration & 0xFF
    payload[2] = right_motor_strength & 0xFF
    payload[3] = left_motor_duration  & 0xFF
    payload[4] = left_motor_strength  & 0xFF
    payload[9] = led_pattern & 0x1E

    wValue_output = WVALUE_HIGH_OUTPUT | 0x01
    try:
        written = global_dev_handle.ctrl_transfer(
            BM_REQUEST_TYPE_SET_FEATURE_HID_CLASS, BREQUEST_SET_REPORT,
            wValue_output, global_intf_num, payload, timeout=1000)
        logger.info(f"Output report sent ({written} bytes)")
    except usb.core.USBError as e:
        logger.error(f"Sending output report failed: {e}")

def _get_raw_report_or_descriptor(bmRequestType: int, bRequest: int, wValue: int, wIndex: int, length: int) -> Optional[bytearray]:
    if global_dev_handle is None or global_intf_num is None:
        logger.error("Device or interface not initialized for raw report/descriptor retrieval.")
        return None
    try:
        return global_dev_handle.ctrl_transfer(
            bmRequestType, bRequest, wValue, wIndex, length, timeout=2000)
    except usb.core.USBError as e:
        logger.error(f"Error in _get_raw_report_or_descriptor for wValue 0x{wValue:04X}: {e}")
        if feature_text_area and feature_text_area.winfo_exists():
            feature_text_area.configure(state='normal')
            feature_text_area.insert(tk.END, f"Error getting raw report/descriptor (wValue 0x{wValue:04X}): {e}\n")
            feature_text_area.see(tk.END); feature_text_area.configure(state='disabled')
        return None
    except Exception as ex:
        logger.error(f"General error in _get_raw_report_or_descriptor for wValue 0x{wValue:04X}: {ex}", exc_info=True)
        if feature_text_area and feature_text_area.winfo_exists():
            feature_text_area.configure(state='normal')
            feature_text_area.insert(tk.END, f"General error getting raw report/descriptor (wValue 0x{wValue:04X}): {ex}\n")
            feature_text_area.see(tk.END); feature_text_area.configure(state='disabled')
        return None

def display_parsed_controller_info() -> None:
    global global_calibration_data
    
    if not global_dev_handle or global_intf_num is None:
        messagebox.showerror("Error","Device not initialized", parent=root)
        return
    if not feature_text_area: return

    feature_text_area.configure(state='normal')
    feature_text_area.delete('1.0', tk.END)
    feature_text_area.insert(tk.END, "--- Controller Info (Feature 0x01) ---\n\n")

    raw = _get_raw_report_or_descriptor(
        BM_REQUEST_TYPE_GET_FEATURE_HID_CLASS, BREQUEST_GET_REPORT,
        WVALUE_HIGH_FEATURE | 0x01, global_intf_num, 64
    )

    if raw is None:
        feature_text_area.insert(tk.END, "Failed to read Feature Report 0x01: No data returned.\n\n")
    elif len(raw) < 2:
        feature_text_area.insert(tk.END, f"Failed to read Feature Report 0x01: Report too short (len {len(raw)}). Raw: {list(raw)}\n\n")
    elif raw[1] != 0x01:
        feature_text_area.insert(tk.END, f"Failed to read Feature Report 0x01 properly. Expected ID 0x01 at index 1, got 0x{raw[1]:02X}. Raw[0]=0x{raw[0]:02X}. Raw: {list(raw)}\n\n")
    else:
        data = list(raw)
        ctype = data[2]
        ct_str = {0x03: "Sixaxis", 0x04: "DualShock 3"}.get(ctype, "Unknown")
        feature_text_area.insert(tk.END, f"Controller Type: {ct_str} (0x{ctype:02X})\n")
        if len(data) >= 6:
            fw = f"v{data[5]}.{data[4]:02d}"
            feature_text_area.insert(tk.END, f"Firmware  : {fw}  (raw 0x{data[5]:02X}{data[4]:02X})\n")
        if len(data) >= 8:
            feature_text_area.insert(tk.END, f"Unknown   : byte 6=0x{data[6]:02X}, byte 7=0x{data[7]:02X}\n")
        if len(data) >= 12:
            lx, ly, rx, ry = data[8:12]
            feature_text_area.insert(tk.END, f"Stick mid : LX={lx}, LY={ly}, RX={rx}, RY={ry}\n")
        if len(data) >= 20:
            cal = data[12:20]
            feature_text_area.insert(tk.END, f"Calib[12–19]: {' '.join(f'0x{x:02X}' for x in cal)}\n")
        if len(data) >= 30:
            dz = data[22:30]
            feature_text_area.insert(tk.END, f"Deadzone/Gain:\n  LX @22,23 = {dz[0]},{dz[1]}\n  LY @24,25 = {dz[2]},{dz[3]}\n  RX @26,27 = {dz[4]},{dz[5]}\n  RY @28,29 = {dz[6]},{dz[7]}\n")
        feature_text_area.insert(tk.END, "\n")

    feature_text_area.insert(tk.END, "--- Bluetooth & ID (Feature 0xF2) ---\n")
    raw2 = _get_raw_report_or_descriptor(BM_REQUEST_TYPE_GET_FEATURE_HID_CLASS, BREQUEST_GET_REPORT, WVALUE_HIGH_FEATURE | 0xF2, global_intf_num, 17)
    if raw2 and len(raw2) >= 17 and raw2[0] == 0xF2:
        bd_addr = parse_mac_address(raw2, 4)
        serial_num = u32_le(raw2, 12)
        pcb_rev = raw2[16]
        feature_text_area.insert(tk.END, f"BD_ADDR   : {bd_addr}\nSerial    : 0x{serial_num:08X} ({serial_num})\nPCB Revision: 0x{pcb_rev:02X}\n")
    else:
        feature_text_area.insert(tk.END, f"Failed to retrieve or parse Feature 0xF2. Raw: {list(raw2) if raw2 else 'None'}\n")

    feature_text_area.insert(tk.END, "\n--- Motion Calib (Feature 0xEF) ---\n")
    global_calibration_data["acc_x_bias"] = 0; global_calibration_data["acc_x_gain"] = 1024
    global_calibration_data["acc_y_bias"] = 0; global_calibration_data["acc_y_gain"] = 1024
    global_calibration_data["acc_z_bias"] = 0; global_calibration_data["acc_z_gain"] = 1024
    global_calibration_data["gyro_z_offset"] = 0
    global_calibration_data["fetched"] = False

    raw3 = _get_raw_report_or_descriptor(BM_REQUEST_TYPE_GET_FEATURE_HID_CLASS, BREQUEST_GET_REPORT, WVALUE_HIGH_FEATURE | 0xEF, global_intf_num, 49)
    
    if raw3 and len(raw3) > 32 and ((raw3[0] == 0xEF) or (len(raw3) > 1 and raw3[1] == 0xEF)):
        global_calibration_data["acc_x_bias"] = s16_le(raw3, 20)
        global_calibration_data["acc_x_gain"] = s16_le(raw3, 22)
        global_calibration_data["acc_y_bias"] = s16_le(raw3, 24)
        global_calibration_data["acc_y_gain"] = s16_le(raw3, 26)
        global_calibration_data["acc_z_bias"] = s16_le(raw3, 28)
        global_calibration_data["acc_z_gain"] = s16_le(raw3, 30)
        global_calibration_data["gyro_z_offset"] = s16_le(raw3, 32)
        global_calibration_data["fetched"] = True
        
        feature_text_area.insert(tk.END, "Successfully fetched and stored calibration data.\n")
        feature_text_area.insert(tk.END, f"Accel Bias/Gain:\n  X: {global_calibration_data['acc_x_bias']} / {global_calibration_data['acc_x_gain']}\n")
        feature_text_area.insert(tk.END, f"  Y: {global_calibration_data['acc_y_bias']} / {global_calibration_data['acc_y_gain']}\n")
        feature_text_area.insert(tk.END, f"  Z: {global_calibration_data['acc_z_bias']} / {global_calibration_data['acc_z_gain']}\n")
        feature_text_area.insert(tk.END, f"Gyro Z-offset: {global_calibration_data['gyro_z_offset']}\n")
        logger.info(f"Fetched calibration data: {global_calibration_data}")
    else:
        feature_text_area.insert(tk.END, f"Failed to retrieve or parse Feature 0xEF. Using default calibration. Raw: {list(raw3) if raw3 else 'None'}\n")
        logger.warning(f"Failed to fetch 0xEF. Using default calibration: {global_calibration_data}")

    feature_text_area.insert(tk.END, "\n")
    feature_text_area.configure(state='disabled')

def get_custom_feature_report_data_gui(report_id_to_get_str: str) -> None:
    if global_dev_handle is None or global_intf_num is None or not feature_text_area:
        messagebox.showerror("Error", "Device not initialized or output area unavailable.", parent=root)
        return
    try:
        report_id_to_get = int(report_id_to_get_str, 0)
    except ValueError:
        messagebox.showerror("Error", "Invalid Report ID format. Use decimal or hex (e.g., 0xF2).", parent=root)
        return
    if not (0 <= report_id_to_get <= 255):
        messagebox.showerror("Error", "Report ID must be between 0 and 255.", parent=root)
        return

    logger.info(f"Manual GET_REPORT (Feature) ID 0x{report_id_to_get:02X}")
    feature_text_area.configure(state='normal')
    feature_text_area.insert(tk.END, f"--- Manual GET Feature Report ID: 0x{report_id_to_get:02X} ---\n")

    length_to_request = 64
    if report_id_to_get == 0xF8:
        length_to_request = 64

    raw_data = _get_raw_report_or_descriptor(
        BM_REQUEST_TYPE_GET_FEATURE_HID_CLASS, BREQUEST_GET_REPORT,
        WVALUE_HIGH_FEATURE | report_id_to_get, global_intf_num, length_to_request
    )

    if raw_data:
        data_list = list(raw_data)
        actual_id_in_payload = data_list[0] if data_list else -1
        msg = f"GET Success. Length: {len(data_list)}.\n  Payload (first byte 0x{actual_id_in_payload:02X}): {str(data_list)}\n"

        if data_list and actual_id_in_payload != report_id_to_get and (len(data_list) > 1 and data_list[1] == report_id_to_get):
             msg += f"  INFO: Requested 0x{report_id_to_get:02X}, payload's first byte is 0x{actual_id_in_payload:02X}, second byte is 0x{data_list[1]:02X} (matches request).\n"
        elif data_list and actual_id_in_payload != report_id_to_get:
             msg += f"  WARNING: Requested 0x{report_id_to_get:02X}, but payload's first byte is 0x{actual_id_in_payload:02X}.\n"

        if report_id_to_get == 0xF8 and len(data_list) >= 4:
             msg += f"  Interpreted 0xF8 (first 4 bytes as example): {data_list[:4]}\n"
        feature_text_area.insert(tk.END, msg)
    else:
        feature_text_area.insert(tk.END, f"GET FAILED or no data returned for Report ID 0x{report_id_to_get:02X}.\n")

    feature_text_area.see(tk.END)
    feature_text_area.configure(state='disabled')

def send_custom_feature_report_data_gui(report_id_to_set_str: str, payload_hex_str: str) -> None:
    if global_dev_handle is None or global_intf_num is None or not feature_text_area:
        messagebox.showerror("Error", "Device not initialized or output area unavailable.", parent=root)
        return

    try:
        report_id_to_set = int(report_id_to_set_str, 0)
    except ValueError:
        messagebox.showerror("Error", "Invalid SET Report ID format. Use decimal or hex (e.g., 0xF4).", parent=root)
        return
    if not (0 <= report_id_to_set <= 255):
        messagebox.showerror("Error", "SET Report ID must be between 0 and 255.", parent=root)
        return

    try:
        actual_payload_data_bytes = binascii.unhexlify(payload_hex_str.replace(" ", "").replace(",", ""))
    except binascii.Error as e_payload:
        messagebox.showerror("Error", f"Invalid payload hex string: {e_payload}", parent=root)
        return
    except Exception as e_payload_general:
        messagebox.showerror("Error", f"Error processing payload: {e_payload_general}", parent=root)
        return


    logger.info(f"Manual SET_REPORT (Feature) ID 0x{report_id_to_set:02X}, Payload: {list(actual_payload_data_bytes)}")
    feature_text_area.configure(state='normal')
    feature_text_area.insert(tk.END, f"--- Manual SET Feature Report ID: 0x{report_id_to_set:02X} ---\n")
    feature_text_area.insert(tk.END, f"  Payload Sent (data phase): {[f'0x{b:02X}' for b in actual_payload_data_bytes]}\n")

    try:
        wValue = WVALUE_HIGH_FEATURE | report_id_to_set
        bytes_written = global_dev_handle.ctrl_transfer(
            BM_REQUEST_TYPE_SET_FEATURE_HID_CLASS, BREQUEST_SET_REPORT,
            wValue, global_intf_num, actual_payload_data_bytes, timeout=1500
        )
        msg = f"SET_REPORT 0x{report_id_to_set:02X} Success! Bytes written (in data phase): {bytes_written}\n"
        feature_text_area.insert(tk.END, msg)
    except usb.core.USBError as e:
        msg = f"SET_REPORT 0x{report_id_to_set:02X} FAILED: {e}\n"
        feature_text_area.insert(tk.END, msg)
    except Exception as ex:
        msg = f"SET_REPORT 0x{report_id_to_set:02X} General Error: {ex}\n"
        feature_text_area.insert(tk.END, msg)
    finally:
        feature_text_area.see(tk.END)
        feature_text_area.configure(state='disabled')

def probe_all_set_reports_gui(root_window: Optional[tk.Tk]) -> None:
    if global_dev_handle is None or global_intf_num is None or not feature_text_area:
        messagebox.showerror("Error", "Device not initialised", parent=root_window)
        return
    
    logger.info("Starting probe_all_set_reports_gui")

def get_and_display_hid_report_descriptor() -> None:
    if global_dev_handle is None or global_intf_num is None or not feature_text_area:
        messagebox.showerror("Error", "Device not initialized or output area unavailable.", parent=root)
        return

    logger.info("Attempting to get HID Report Descriptor...")
    feature_text_area.configure(state='normal')
    feature_text_area.delete('1.0', tk.END)
    feature_text_area.insert(tk.END, "--- HID Report Descriptor ---\n")

    try:
        wValue_hid_report_desc = WVALUE_HIGH_REPORT_DESC | 0x00
        length_to_request = 512

        hid_report_desc_bytes = _get_raw_report_or_descriptor(
            BM_REQUEST_TYPE_GET_DESCRIPTOR_STD_INTERFACE,
            BREQUEST_GET_DESCRIPTOR,
            wValue_hid_report_desc,
            global_intf_num,
            length_to_request
        )

        if hid_report_desc_bytes:
            data_list = list(hid_report_desc_bytes)
            feature_text_area.insert(tk.END, f"HID Report Descriptor (Length: {len(data_list)} bytes):\n")
            hex_lines = [
                f"{i:04X}: {' '.join(f'{b:02X}' for b in data_list[i:i+16])}"
                for i in range(0, len(data_list), 16)
            ]
            feature_text_area.insert(tk.END, "\n".join(hex_lines) + "\n\n")
        else:
            feature_text_area.insert(tk.END, "Failed to retrieve HID Report Descriptor or it was empty.\n")
    except Exception as e:
        feature_text_area.insert(tk.END, f"Error getting HID Report Descriptor: {e}\n")
        logger.error(f"Exception in get_and_display_hid_report_descriptor: {e}", exc_info=True)
    finally:
        feature_text_area.see(tk.END)
        feature_text_area.configure(state='disabled')

def usb_polling_loop() -> None:
    global last_polling_timestamp, current_polling_rate_hz, latest_input_data
    global input_reports_enabled, global_dev_handle, endpoint_desc

    consecutive_timeouts = 0
    MAX_CONSECUTIVE_TIMEOUTS = 5

    while True:
        if global_dev_handle is None or endpoint_desc is None:
            if input_reports_enabled:
                logger.warning("Polling loop: Device or endpoint not available. Disabling polling.")
                input_reports_enabled = False

            if current_polling_rate_hz != 0.0 or last_polling_timestamp is not None or len(polling_intervals) > 0:
                current_polling_rate_hz = 0.0
                last_polling_timestamp = None
                polling_intervals.clear()
            if latest_input_data["data"] is not None:
                 latest_input_data["data"] = None
            
            consecutive_timeouts = 0
            time.sleep(0.5)
            continue

        if input_reports_enabled:
            try:
                data = global_dev_handle.read(endpoint_desc.bEndpointAddress, endpoint_desc.wMaxPacketSize, timeout=200)
                consecutive_timeouts = 0
                
                current_time = time.time()

                if last_polling_timestamp is not None:
                    interval = current_time - last_polling_timestamp
                    if 0.001 < interval < 2.0:
                        polling_intervals.append(interval)
                        avg_interval = sum(polling_intervals) / len(polling_intervals)
                        current_polling_rate_hz = 1.0 / avg_interval if avg_interval > 0.0001 else 0.0
                last_polling_timestamp = current_time
                latest_input_data["data"] = list(data) if data else None

            except usb.core.USBError as e:
                latest_input_data["data"] = None
                if e.errno in _WIN_TIMEOUTS or 'TIMEOUT' in str(e).upper():
                    consecutive_timeouts += 1
                    if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                        if current_polling_rate_hz != 0.0:
                            logger.warning(f"Polling: {MAX_CONSECUTIVE_TIMEOUTS} consecutive timeouts. Resetting rate. (PS Button may be needed)")
                        current_polling_rate_hz = 0.0
                        last_polling_timestamp = None
                        polling_intervals.clear()
                elif e.errno == 19 or 'NO_DEVICE' in str(e).upper() or \
                    e.errno == 5 or 'IO' in str(e).upper():
                    logger.error(f"USBError: Device disconnected or I/O error. Stopping polling. {e}")
                    global_dev_handle = None
                    endpoint_desc = None
                    input_reports_enabled = False
                    
                    if current_paired_mac_var and root and root.winfo_exists():
                        root.after(0, lambda: current_paired_mac_var.set("N/A (Disconnected)"))
                    
                    current_polling_rate_hz = 0.0
                    last_polling_timestamp = None
                    polling_intervals.clear()
                    consecutive_timeouts = 0
                else:
                    logger.error(f"USBError in polling loop: {e}. Device may be unstable. Stopping polling.")
                    global_dev_handle = None
                    endpoint_desc = None
                    input_reports_enabled = False
                    
                    if current_paired_mac_var and root and root.winfo_exists():
                        root.after(0, lambda: current_paired_mac_var.set("N/A (Error)"))
                    
                    current_polling_rate_hz = 0.0
                    last_polling_timestamp = None
                    polling_intervals.clear()
                    consecutive_timeouts = 0
            except Exception as e_poll:
                logger.error(f"Unexpected error in polling loop: {e_poll}", exc_info=True)
                global_dev_handle = None
                endpoint_desc = None
                input_reports_enabled = False
                latest_input_data["data"] = None
                current_polling_rate_hz = 0.0
                last_polling_timestamp = None
                polling_intervals.clear()
                consecutive_timeouts = 0
        else:
            if current_polling_rate_hz != 0.0 or last_polling_timestamp is not None or len(polling_intervals) > 0:
                current_polling_rate_hz = 0.0
                last_polling_timestamp = None
                polling_intervals.clear()
            if latest_input_data.get("data") is not None:
                latest_input_data["data"] = None
            consecutive_timeouts = 0
            time.sleep(0.1)
            
def restart_controller_cmd() -> None:
    if global_dev_handle is None or global_intf_num is None:
        messagebox.showerror("Error", "Device not initialized. Cannot restart.", parent=root)
        return
    try:
        wValue = WVALUE_HIGH_FEATURE | 0xF4
        logger.info(f"Sending restart command (Payload {list(RESTART_CONTROLLER_PAYLOAD)}) to controller.")
        global_dev_handle.ctrl_transfer(
            BM_REQUEST_TYPE_SET_FEATURE_HID_CLASS, BREQUEST_SET_REPORT,
            wValue, global_intf_num, RESTART_CONTROLLER_PAYLOAD, timeout=1000
        )
        logger.info("Restart report sent. Controller may disconnect and attempt to reconnect shortly.")
    except usb.core.USBError as e:
        logger.error(f"Failed to send restart report: {e}")

def cmd_enable_input_reports() -> None:
    global input_reports_enabled
    if global_dev_handle is None or global_intf_num is None:
        messagebox.showerror("Error", "Device not initialized. Cannot enable reports.", parent=root)
        return
    try:
        wValue = WVALUE_HIGH_FEATURE | 0xF4
        global_dev_handle.ctrl_transfer(
            BM_REQUEST_TYPE_SET_FEATURE_HID_CLASS, BREQUEST_SET_REPORT,
            wValue, global_intf_num, ENABLE_INPUT_STREAMING_PAYLOAD, timeout=1000
        )
        input_reports_enabled = True
        logger.info(f"Input reports ENABLE command (Payload {list(ENABLE_INPUT_STREAMING_PAYLOAD)}) sent. Press PS Button if input doesn't start.")
    except usb.core.USBError as e:
        messagebox.showerror("USB Error", f"Failed to enable input reports: {e}\nAre you changed the driver to WinUsb?\nIf not, install WinUsb using Zadig (Just google Zadig using your browser)", parent=root)
        logger.error(f"USBError enabling input reports: {e}")

def cmd_disable_input_reports() -> None:
    global input_reports_enabled
    if global_dev_handle is None or global_intf_num is None:
        messagebox.showerror("Error", "Device not initialized. Cannot disable reports.", parent=root)
        return
    try:
        wValue = WVALUE_HIGH_FEATURE | 0xF4
        global_dev_handle.ctrl_transfer(
            BM_REQUEST_TYPE_SET_FEATURE_HID_CLASS, BREQUEST_SET_REPORT,
            wValue, global_intf_num, DISABLE_INPUT_STREAMING_PAYLOAD, timeout=1000
        )
        input_reports_enabled = False
        logger.info(f"Input reports DISABLE command (Payload {list(DISABLE_INPUT_STREAMING_PAYLOAD)}) sent.")
    except usb.core.USBError as e:
        messagebox.showerror("USB Error", f"Failed to disable input reports: {e}", parent=root)
        logger.error(f"USBError disabling input reports: {e}")

def initialize_controller() -> bool:
    global global_dev_handle, global_intf_num, endpoint_desc
    global global_calibration_data
    global current_stick_calib_flash_address, current_stick_calib_data_total_len, current_stick_calib_num_main_bytes, detected_stick_type 

    if global_dev_handle:
        logger.info("Controller already initialized.")
        return True

    current_dev: Optional[usb.core.Device] = None
    current_intf_num: Optional[int] = None

    if connection_status_label and connection_status_label.winfo_exists():
        connection_status_label.config(text="Connecting to DS3...")
    logger.info("Attempting to initialize DS3 controller...")

    current_stick_calib_flash_address = DEFAULT_4PIN_CALIB_FLASH_START_ADDRESS
    current_stick_calib_data_total_len = DEFAULT_4PIN_CALIB_DATA_TOTAL_LEN
    current_stick_calib_num_main_bytes = DEFAULT_4PIN_CALIB_NUM_MAIN_BYTES
    detected_stick_type = None
    logger.info("INIT: Resetting current stick calibration parameters to default (4-pin initial detection mode).")

    try:
        dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if dev is None:
            logger.info("DS3 controller not found.")
            if connection_status_label and connection_status_label.winfo_exists():
                connection_status_label.config(text="Disconnected (Not Found)")
            
            if current_paired_mac_var and root and root.winfo_exists():
                root.after(0, lambda: current_paired_mac_var.set("N/A (Not Found)"))
            
            return False

        current_dev = dev
        logger.info(f"Found DS3: Bus {dev.bus} Device {dev.address} ({dev.idVendor:04x}:{dev.idProduct:04x})")

        try:
            dev.set_configuration()
        except usb.core.USBError as e:
            if e.errno == 16 or 'BUSY' in str(e).upper():
                logger.debug("Device already configured or resource busy, proceeding.")
            else:
                logger.error(f"Failed to set_configuration: {e}")
                return False

        cfg = dev.get_active_configuration()
        if cfg is None:
            logger.error("Could not get active configuration.")
            return False
        
        try:
            intf_desc = cfg[(0,0)] 
        except IndexError:
            logger.error(f"Could not get interface descriptor for (0,0) in config {cfg.bConfigurationValue}.")
            return False

        current_intf_num = intf_desc.bInterfaceNumber
        logger.debug(f"Using configuration {cfg.bConfigurationValue}, interface {current_intf_num}")

        if hasattr(dev, 'is_kernel_driver_active'):
            try:
                if dev.is_kernel_driver_active(current_intf_num):
                    logger.info(f"Detaching kernel driver from interface {current_intf_num}...")
                    dev.detach_kernel_driver(current_intf_num)
            except Exception as e_detach:
                logger.warning(f"Could not detach kernel driver (may not be critical): {e_detach}")

        try:
            usb.util.claim_interface(dev, current_intf_num)
            logger.info(f"Interface {current_intf_num} claimed.")
        except usb.core.USBError as e:
            if e.errno != 16 and 'BUSY' not in str(e).upper():
                logger.error(f"Failed to claim interface {current_intf_num}: {e}")
                return False
            else:
                logger.debug(f"Interface {current_intf_num} already claimed or busy, proceeding: {e}")

        init_f2_ok, init_f4_outputs_ok = False, False
        if _sony_set_operational(dev, current_intf_num):
            init_f2_ok = True
            time.sleep(0.05)
            if _enable_outputs_and_sensors(dev, current_intf_num):
                init_f4_outputs_ok = True

        if not init_f2_ok:
            logger.critical("CRITICAL: GET_REPORT 0xF2 (wake-up command) FAILED. Controller may not respond.")
        if not init_f4_outputs_ok:
            logger.warning("SET_REPORT 0xF4 (enable outputs/sensors) FAILED. LEDs/Rumble/Sensors might not work.")

        ep_in = usb.util.find_descriptor(intf_desc,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN and \
                                  usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_INTR)
        if ep_in is None:
            logger.error("Interrupt IN endpoint not found on the interface!")
            try: usb.util.release_interface(dev, current_intf_num)
            except: pass
            return False

        logger.info(f"Interrupt IN endpoint 0x{ep_in.bEndpointAddress:02x} found (MaxPacketSize: {ep_in.wMaxPacketSize}B)")

        global_dev_handle = current_dev
        global_intf_num = current_intf_num
        endpoint_desc = ep_in

        cmd_enable_input_reports()

        fetch_stick_calibration_data() 

        if root and root.winfo_exists():
             root.after(100, display_parsed_controller_info)
             root.after(150, determine_stick_type_and_update_ui)
             root.after(200, fetch_and_display_current_paired_mac) 
        else:
             raw_calib = _get_raw_report_or_descriptor(BM_REQUEST_TYPE_GET_FEATURE_HID_CLASS, BREQUEST_GET_REPORT, WVALUE_HIGH_FEATURE | 0xEF, global_intf_num, 49)
             if raw_calib and len(raw_calib) > 32 and ((raw_calib[0] == 0xEF) or (len(raw_calib) > 1 and raw_calib[1] == 0xEF)):
                global_calibration_data["acc_x_bias"] = s16_le(raw_calib, 20); global_calibration_data["acc_x_gain"] = s16_le(raw_calib, 22)
                global_calibration_data["acc_y_bias"] = s16_le(raw_calib, 24); global_calibration_data["acc_y_gain"] = s16_le(raw_calib, 26)
                global_calibration_data["acc_z_bias"] = s16_le(raw_calib, 28); global_calibration_data["acc_z_gain"] = s16_le(raw_calib, 30)
                global_calibration_data["gyro_z_offset"] = s16_le(raw_calib, 32)
                global_calibration_data["fetched"] = True
                logger.info(f"Silently fetched calibration data on connect: {global_calibration_data}")
             else:
                logger.warning("Failed to silently fetch 0xEF on connect. Using defaults.")

        logger.info(">>> DS3 Initialized. Press PS button if input reports do not start. <<<")

        if init_f4_outputs_ok:
            set_led_and_rumble(led_pattern=0x02,
                               right_motor_duration=0, right_motor_strength=0,
                               left_motor_duration=0,  left_motor_strength=0)
        
        return True

    except Exception as e_init:
        logger.error(f"Generic error during controller initialization: {e_init}", exc_info=True)
        if current_dev and current_intf_num is not None:
            try: usb.util.release_interface(current_dev, current_intf_num)
            except Exception as e_release: logger.debug(f"Error releasing interface during init cleanup: {e_release}")
        global_dev_handle = None
        global_intf_num = None
        endpoint_desc = None
        if connection_status_label and connection_status_label.winfo_exists():
            connection_status_label.config(text="Disconnected (Error)")
        
        if current_paired_mac_var and root and root.winfo_exists():
            root.after(0, lambda: current_paired_mac_var.set("N/A (Error)"))
        
        return False

def enable_controller_ui(is_enabled: bool) -> None:
    for widget in ui_controls_to_toggle:
        if not (widget and widget.winfo_exists()):
            continue
        
        try:
            if hasattr(widget, 'state') and hasattr(widget, 'instate'):
                is_currently_disabled = widget.instate(['disabled'])
                
                if is_enabled:
                    if is_currently_disabled:
                        widget.state(['!disabled'])
                else:
                    if not is_currently_disabled:
                        widget.state(['disabled'])
            
            elif hasattr(widget, 'configure') and 'state' in widget.configure():
                current_tk_state = str(widget.cget('state'))
                is_currently_disabled_std_tk = (current_tk_state == tk.DISABLED)

                if is_enabled:
                    if is_currently_disabled_std_tk:
                        widget.configure(state=tk.NORMAL)
                else:
                    if not is_currently_disabled_std_tk:
                        widget.configure(state=tk.DISABLED)
                        
        except tk.TclError as e:
            logger.debug(f"TclError operating on widget state for {widget.winfo_class()}: {e}", exc_info=False)
        except Exception as ex:
            logger.warning(f"Unexpected error operating on widget state for {widget.winfo_class()}: {ex}", exc_info=True)

def _do_repeat_adjust() -> None:
    global _repeat_job_id, _repeating_byte_index, _repeating_delta, root
    if _repeating_byte_index is not None and _repeating_delta is not None and root and root.winfo_exists():
        adjust_stick_calib_byte(_repeating_byte_index, _repeating_delta)
        _repeat_job_id = root.after(_subsequent_repeat_delay_ms, _do_repeat_adjust)
    else:
        stop_repeat_adjust()

def start_repeat_adjust(event: tk.Event, byte_index: int, delta: int) -> None:
    global _repeat_job_id, _repeating_byte_index, _repeating_delta, root
    
    stop_repeat_adjust()

    if root is None or not root.winfo_exists():
        return

    _repeating_byte_index = byte_index
    _repeating_delta = delta

    adjust_stick_calib_byte(byte_index, delta)

    _repeat_job_id = root.after(_initial_repeat_delay_ms, _do_repeat_adjust)

def stop_repeat_adjust(event: Optional[tk.Event] = None) -> None:
    global _repeat_job_id, _repeating_byte_index, _repeating_delta, root
    if _repeat_job_id and root and root.winfo_exists():
        try:
            root.after_cancel(_repeat_job_id)
        except ValueError: 
            logger.debug("Could not cancel repeat job (might have already run or invalid ID).")
        except tk.TclError:
            logger.debug("TclError cancelling repeat job, root might be shutting down.")

    _repeat_job_id = None
    _repeating_byte_index = None
    _repeating_delta = None

def _initiate_hold_action() -> None:
    global _adjust_hold_init_job_id, _adjust_repeat_job_id, _adjust_byte_index, _adjust_delta, root

    _adjust_hold_init_job_id = None

    if _adjust_byte_index is None or _adjust_delta is None or not root or not root.winfo_exists():
        handle_button_release_for_adjust()
        return

    adjust_stick_calib_byte(_adjust_byte_index, _adjust_delta)

    _adjust_repeat_job_id = root.after(HOLD_FIRST_REPEAT_DELAY_MS, _perform_repeating_adjust)

def _perform_repeating_adjust() -> None:
    global _adjust_repeat_job_id, _adjust_byte_index, _adjust_delta, root

    if _adjust_byte_index is None or _adjust_delta is None or not root or not root.winfo_exists():
        handle_button_release_for_adjust()
        return

    adjust_stick_calib_byte(_adjust_byte_index, _adjust_delta)
    _adjust_repeat_job_id = root.after(HOLD_SUBSEQUENT_REPEAT_DELAY_MS, _perform_repeating_adjust)

def handle_button_press_for_adjust(event: tk.Event, byte_index: int, delta: int) -> None:
    global _adjust_press_time, _adjust_hold_init_job_id, _adjust_byte_index, _adjust_delta, root

    handle_button_release_for_adjust()

    if root is None or not root.winfo_exists():
        return

    _adjust_byte_index = byte_index
    _adjust_delta = delta
    _adjust_press_time = time.monotonic()

    _adjust_hold_init_job_id = root.after(CLICK_TIME_THRESHOLD_MS, _initiate_hold_action)

def handle_button_release_for_adjust(event: Optional[tk.Event] = None) -> None:
    global _adjust_press_time, _adjust_hold_init_job_id, _adjust_repeat_job_id
    global _adjust_byte_index, _adjust_delta, root

    was_click_action_performed = False

    if _adjust_hold_init_job_id and root and root.winfo_exists():
        try:
            root.after_cancel(_adjust_hold_init_job_id)
        except ValueError:
            logger.debug("Could not cancel hold_init_job (might have already run or invalid ID).")
        except tk.TclError:
            logger.debug("TclError cancelling hold_init_job, root might be shutting down.")
        _adjust_hold_init_job_id = None

        if _adjust_press_time is not None and _adjust_byte_index is not None and _adjust_delta is not None:
            logger.debug(f"Button released quickly: Performing single click action for byte {_adjust_byte_index}.")
            adjust_stick_calib_byte(_adjust_byte_index, _adjust_delta)
            was_click_action_performed = True

    if _adjust_repeat_job_id and root and root.winfo_exists():
        try:
            root.after_cancel(_adjust_repeat_job_id)
        except ValueError:
            logger.debug("Could not cancel repeat_job (might have already run or invalid ID).")
        except tk.TclError:
            logger.debug("TclError cancelling repeat_job, root might be shutting down.")
        _adjust_repeat_job_id = None
        if not was_click_action_performed:
             logger.debug(f"Button released: Stopping repeat action for byte {_adjust_byte_index}.")

    _adjust_press_time = None
    _adjust_hold_init_job_id = None
    _adjust_repeat_job_id = None
    _adjust_byte_index = None
    _adjust_delta = None

def _safe_restart_controller_cmd() -> None:
    global _calib_write_pending_id, _calib_write_lock, root

    if _calib_write_pending_id is not None:
        messagebox.showwarning(
            "Restart Blocked",
            "A stick calibration write is scheduled to occur shortly.\n"
            "Please wait a moment for it to complete before restarting the controller.",
            parent=root
        )
        logger.warning("Restart controller command blocked: Calibration write is scheduled (_calib_write_pending_id is set).")
        return

    if _calib_write_lock.locked():
        messagebox.showwarning(
            "Restart Blocked",
            "A stick calibration write is currently in progress.\n"
            "Please wait for it to complete before restarting the controller.",
            parent=root
        )
        logger.warning("Restart controller command blocked: Calibration write is in progress (_calib_write_lock is locked).")
        return
    
    # If no pending or active write, proceed with the actual restart command
    restart_controller_cmd()

def main_gui_app() -> None:
    global root, polling_rate_label, connection_status_label
    global canvas_left, canvas_right, dot_left, dot_right, center, radius
    global button_vars, ps_var, pressure_map, pressure_vars, sensor_vars, feature_text_area
    global ui_controls_to_toggle, data_log_toggle_button
    global data_logging_enabled
    global host_mac_entry_var, current_paired_mac_var
    global stick_calib_display_vars, stick_calib_axis_value_vars, stick_calib_frame

    root = tk.Tk()
    root.title("DS3 Input & Report Inspector v1.0.0")

    window_width = 1200
    window_height = 900

    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()

    center_x = int(screen_width / 2 - window_width / 2)
    center_y = int(screen_height / 2 - window_height / 2)

    root.geometry(f'{window_width}x{window_height}+{center_x}+{center_y}')

    BG_COLOR = '#1e1e1e'
    FG_COLOR = '#d4d4d4'
    FIELD_BG_COLOR = '#2a2a2a'
    BUTTON_BG_COLOR = '#3c3c3c'
    BUTTON_ACTIVE_BG_COLOR = '#4f4f4f'
    BUTTON_PRESSED_BG_COLOR = FIELD_BG_COLOR
    BORDER_COLOR = '#4a4a4a'
    ACCENT_COLOR = '#007acc'
    TROUGH_COLOR = '#2c2c2c'
    DISABLED_FG_COLOR = '#666666'

    root.configure(bg=BG_COLOR)

    logger.info("Applying modernized TTK black theme.")
    style = ttk.Style(root)
    try:
        style.theme_use('clam')
    except tk.TclError:
        logger.warning("Failed to set 'clam' theme, using default. Custom styles might not apply perfectly.")

    style.configure('.',
                    background=BG_COLOR,
                    foreground=FG_COLOR,
                    borderwidth=0,
                    relief=tk.FLAT)
    style.map('.',
              foreground=[('disabled', DISABLED_FG_COLOR)])

    style.configure('TFrame', background=BG_COLOR)
    style.configure('TLabelframe',
                    background=BG_COLOR,
                    bordercolor=BORDER_COLOR,
                    borderwidth=1,
                    relief=tk.SOLID)
    style.configure('TLabelframe.Label',
                    background=BG_COLOR,
                    foreground=FG_COLOR,
                    padding=(5, 2))

    style.configure('TButton',
                    background=BUTTON_BG_COLOR,
                    foreground=FG_COLOR,
                    padding=(8, 5),
                    relief=tk.FLAT,
                    borderwidth=1,
                    bordercolor=BORDER_COLOR)
    style.map('TButton',
              background=[('pressed', BUTTON_PRESSED_BG_COLOR),
                          ('active', BUTTON_ACTIVE_BG_COLOR)],
              foreground=[('disabled', DISABLED_FG_COLOR),
                          ('pressed', FG_COLOR),
                          ('active', FG_COLOR)],
              bordercolor=[('pressed', BORDER_COLOR),
                           ('active', ACCENT_COLOR),
                           ('focus', ACCENT_COLOR)],
              relief=[('pressed', tk.SUNKEN),
                      ('!pressed', tk.FLAT)])

    style.configure('TCheckbutton',
                    background=BG_COLOR,
                    foreground=FG_COLOR,
                    indicatorrelief=tk.FLAT,
                    padding=3)
    style.map('TCheckbutton',
              indicatorcolor=[('selected', ACCENT_COLOR),
                              ('disabled', FIELD_BG_COLOR),
                              ('!selected', FIELD_BG_COLOR),
                              ('active', FIELD_BG_COLOR)],
              foreground=[('selected', FG_COLOR),
                          ('disabled', DISABLED_FG_COLOR)],
              background=[('active', BG_COLOR)])
    
    style.configure('Horizontal.TScale',
                    background=BG_COLOR,
                    troughcolor=TROUGH_COLOR,
                    sliderrelief=tk.FLAT,
                    sliderthickness=15,
                    borderwidth=0)
    style.map('Horizontal.TScale',
              background=[('active', ACCENT_COLOR)],
              troughcolor=[('disabled', TROUGH_COLOR)])

    style.configure('Horizontal.TProgressbar',
                    background=ACCENT_COLOR,
                    troughcolor=TROUGH_COLOR,
                    relief=tk.FLAT,
                    borderwidth=0,
                    thickness=12)

    style.configure('TEntry',
                    fieldbackground=FIELD_BG_COLOR,
                    foreground=FG_COLOR,
                    insertcolor=FG_COLOR,
                    relief=tk.FLAT,
                    borderwidth=1,
                    bordercolor=BORDER_COLOR,
                    padding=3)
    style.map('TEntry',
              bordercolor=[('focus', ACCENT_COLOR)],
              fieldbackground=[('disabled', BG_COLOR)])

    style.configure('TPanedwindow', background=BG_COLOR)
    style.configure('Sash',
                    background=BUTTON_BG_COLOR,
                    borderwidth=0,
                    relief=tk.FLAT,
                    gripcount=0)
    style.map('Sash', background=[('active', ACCENT_COLOR)])

    CANVAS_BG = BG_COLOR
    TEXT_AREA_BG = FIELD_BG_COLOR
    TEXT_AREA_FG = FG_COLOR

    top_status_frame = ttk.Frame(root, padding=(5,5,5,0))
    top_status_frame.pack(side="top", fill="x")
    connection_status_label = ttk.Label(top_status_frame, text="Disconnected", font=("Segoe UI", 10))
    connection_status_label.pack(side="left", padx=5, pady=2)
    polling_rate_label = ttk.Label(top_status_frame, text="Polling Rate: 0 Hz", font=("Segoe UI", 10))
    polling_rate_label.pack(side="right", padx=5, pady=2)

    main_horizontal_paned_window = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
    main_horizontal_paned_window.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    left_panel = ttk.Frame(main_horizontal_paned_window, padding=10)

    center_right_paned_window = ttk.PanedWindow(main_horizontal_paned_window, orient=tk.HORIZONTAL)

    center_panel = ttk.Frame(center_right_paned_window, padding=10)

    right_panel = ttk.Frame(center_right_paned_window, padding=10)

    main_horizontal_paned_window.add(left_panel, weight=1) 
    main_horizontal_paned_window.add(center_right_paned_window, weight=2)

    center_right_paned_window.add(center_panel, weight=1)
    center_right_paned_window.add(right_panel, weight=1)

    stick_calib_frame = ttk.LabelFrame(left_panel, text="Stick Center Calibration", padding=10)
    stick_calib_frame.pack(pady=(5, 5), fill="x", anchor="n")

    stick_calib_display_vars = [tk.StringVar(value="N/A") for _ in range(DEFAULT_4PIN_CALIB_DATA_TOTAL_LEN)] 
    stick_calib_axis_value_vars = [tk.StringVar(value="----") for _ in range(4)]

    _update_stick_calib_ui_elements()

    stick_display_frame = ttk.LabelFrame(center_panel, text="Analog Sticks", padding=20)
    stick_display_frame.pack(pady=5, fill="x", anchor="n")
    stick_canvas_container = ttk.Frame(stick_display_frame)
    stick_canvas_container.pack()

    canvas_size, stick_dot_radius_val = 120, 3
    center, radius = canvas_size / 2.0, float(stick_dot_radius_val)
    outer_circle_radius = center - 0.75

    canvas_left = tk.Canvas(stick_canvas_container, width=canvas_size, height=canvas_size, bg=CANVAS_BG, highlightthickness=0)
    canvas_left.pack(side='left', padx=10)
    canvas_left.create_line(center,0,center,canvas_size, fill='grey50')
    canvas_left.create_line(0,center,canvas_size,center, fill='grey50')
    canvas_left.create_oval(center - outer_circle_radius, center - outer_circle_radius,
                        center + outer_circle_radius, center + outer_circle_radius,
                        outline='grey50', width=1)
    dot_left = canvas_left.create_oval(center-radius,center-radius,center+radius,center+radius, fill='red', outline=FG_COLOR)

    canvas_right = tk.Canvas(stick_canvas_container, width=canvas_size, height=canvas_size, bg=CANVAS_BG, highlightthickness=0)
    canvas_right.pack(side='left', padx=10)
    canvas_right.create_line(center,0,center,canvas_size, fill='grey50')
    canvas_right.create_line(0,center,canvas_size,center, fill='grey50')
    canvas_right.create_oval(center - outer_circle_radius, center - outer_circle_radius,
                             center + outer_circle_radius, center + outer_circle_radius,
                             outline='grey50', width=1)
    dot_right = canvas_right.create_oval(center-radius,center-radius,center+radius,center+radius, fill='blue', outline=FG_COLOR)

    button_frame = ttk.LabelFrame(center_panel, text="Button Press Test", padding=5)
    button_frame.pack(pady=5, fill="x")
    btn_defs: List[Tuple[str, int]] = [
        ("Select", 1<<0), ("L3", 1<<1), ("R3", 1<<2), ("Start", 1<<3),
        ("Up", 1<<4), ("Right",1<<5),("Down", 1<<6), ("Left", 1<<7),
        ("L2", 1<<0), ("R2", 1<<1), ("L1", 1<<2), ("R1", 1<<3),
        ("Triangle",1<<4), ("Circle",1<<5),("Cross",1<<6),("Square",1<<7)
    ]
    num_btn_cols = 4
    button_vars = []
    for i, (name, mask) in enumerate(btn_defs):
        var = tk.IntVar()
        is_for_byte_0 = (i < 8)
        cb = ttk.Checkbutton(button_frame, text=name, variable=var, state=tk.DISABLED)
        cb.grid(row=i // num_btn_cols, column=i % num_btn_cols, sticky="w", padx=3, pady=2)
        button_vars.append((var, mask, is_for_byte_0))

    ps_var = tk.IntVar()
    ps_cb = ttk.Checkbutton(button_frame, text="PS", variable=ps_var, state=tk.DISABLED)
    ps_cb.grid(row=(len(btn_defs) // num_btn_cols), column=0, columnspan=num_btn_cols, sticky="w", padx=3, pady=5)

    for i in range(num_btn_cols): button_frame.columnconfigure(i, weight=1)

    analog_frame = ttk.LabelFrame(center_panel, text="Analog & Pressure Test", padding=5)
    analog_frame.pack(pady=5, fill="x")
    pressure_map = {
        "Square": 25, "Cross": 24, "Circle": 23, "Triangle": 22,
        "R1": 21, "L1": 20, "R2": 19, "L2": 18,
        "Up": 14, "Right": 15, "Down": 16, "Left": 17
    }
    pressure_vars = {}
    num_pressure_pairs_per_row = 2
    pressure_map_items = list(pressure_map.items())
    for idx, (name, _) in enumerate(pressure_map_items):
        row_idx = idx // num_pressure_pairs_per_row
        col_start = (idx % num_pressure_pairs_per_row) * 2
        pressure_vars[name] = tk.IntVar()
        ttk.Label(analog_frame, text=f"{name}:").grid(row=row_idx, column=col_start, sticky="e", padx=(0,2))
        pb = ttk.Progressbar(analog_frame, orient="horizontal", length=100, maximum=255, variable=pressure_vars[name])
        pb.grid(row=row_idx, column=col_start + 1, sticky="ew", padx=(0,10), pady=1)
    for i in range(num_pressure_pairs_per_row * 2):
        analog_frame.columnconfigure(i, weight=(1 if i % 2 == 1 else 0))

    motion_container_frame = ttk.Frame(center_panel, padding=(0, 5))
    motion_container_frame.pack(pady=10, fill="x", anchor="n")

    motion_container_frame.grid_columnconfigure(0, weight=1)
    motion_container_frame.grid_columnconfigure(1, weight=1)

    for _, key, _ in GYRO_AXES:
        sensor_vars[key] = tk.StringVar(value="N/A")
    for _, key, _ in ACCEL_AXES: 
        sensor_vars[key] = tk.StringVar(value="N/A")

    accel_labelframe = ttk.LabelFrame(motion_container_frame, text="Accelerometer", padding=(10,5))
    accel_labelframe.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

    row_offset_accel = 0
    for i, (text, key, color) in enumerate(ACCEL_AXES):
        label_widget = ttk.Label(accel_labelframe, text=text, foreground=color)
        label_widget.grid(row=row_offset_accel + i, column=0, sticky="w", padx=(5,10), pady=2)
        
        value_widget = ttk.Label(accel_labelframe, textvariable=sensor_vars[key], foreground=color, width=15, anchor="e")
        value_widget.grid(row=row_offset_accel + i, column=1, sticky="e", padx=5, pady=2)

    accel_labelframe.grid_columnconfigure(0, weight=0)
    accel_labelframe.grid_columnconfigure(1, weight=1)

    gyro_labelframe = ttk.LabelFrame(motion_container_frame, text="Gyroscope", padding=(10,5))
    gyro_labelframe.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

    row_offset_gyro = 0
    for i, (text, key, color) in enumerate(GYRO_AXES):
        label_widget = ttk.Label(gyro_labelframe, text=text, foreground=color)
        label_widget.grid(row=row_offset_gyro + i, column=0, sticky="w", padx=(5,10), pady=2)

        value_widget = ttk.Label(gyro_labelframe, textvariable=sensor_vars[key], foreground=color, width=12, anchor="e")
        value_widget.grid(row=row_offset_gyro + i, column=1, sticky="e", padx=5, pady=2)

    gyro_labelframe.grid_columnconfigure(0, weight=0)
    gyro_labelframe.grid_columnconfigure(1, weight=1)

    output_controls_frame = ttk.LabelFrame(right_panel, text="LED & Rumble Controls", padding=10)
    output_controls_frame.pack(pady=5, fill="x")
    led_vars: List[tk.IntVar] = [tk.IntVar() for _ in range(4)]
    led_checkbox_frame = ttk.Frame(output_controls_frame)
    led_checkbox_frame.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0,5))
    for i, var in enumerate(led_vars):
        cb = ttk.Checkbutton(led_checkbox_frame, text=f"LED {i+1}", variable=var)
        cb.pack(side="left", padx=10, expand=True)
        ui_controls_to_toggle.append(cb)

    ttk.Label(output_controls_frame, text="Duration (Both Motors):").grid(row=1, column=0, columnspan=2, sticky="w", pady=(5,0))
    ui_duration = tk.IntVar(value=0)
    scale_duration = ttk.Scale(output_controls_frame, from_=0, to=255, variable=ui_duration, orient="horizontal", style="Horizontal.TScale")
    scale_duration.grid(row=2, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
    ui_controls_to_toggle.append(scale_duration)

    ttk.Label(output_controls_frame, text="Strength (Both Motors):").grid(row=1, column=2, columnspan=2, sticky="w", pady=(5,0))
    ui_strength = tk.IntVar(value=0)
    scale_strength = ttk.Scale(output_controls_frame, from_=0, to=255, variable=ui_strength, orient="horizontal", style="Horizontal.TScale")
    scale_strength.grid(row=2, column=2, columnspan=2, sticky="ew", padx=2, pady=2)
    ui_controls_to_toggle.append(scale_strength)

    def apply_outputs_cmd() -> None:
        if global_dev_handle is None: return
        pattern = 0
        if led_vars[0].get(): pattern |= 0x02
        if led_vars[1].get(): pattern |= 0x04
        if led_vars[2].get(): pattern |= 0x08
        if led_vars[3].get(): pattern |= 0x10
        dur, powr = ui_duration.get(), ui_strength.get()
        if powr == 0: dur = 0
        if dur == 0: powr = 0
        set_led_and_rumble(pattern, dur, powr, dur, powr)

    apply_btn = ttk.Button(output_controls_frame, text="Apply", command=apply_outputs_cmd)
    apply_btn.grid(row=3, column=0, columnspan=2, pady=10, sticky="ew", padx=2)
    ui_controls_to_toggle.append(apply_btn)

    all_off_btn = ttk.Button(output_controls_frame, text="All Off", command=lambda: (
        ui_duration.set(0), ui_strength.set(0),
        *(v.set(0) for v in led_vars),
        set_led_and_rumble(0,0,0,0,0)
    ))
    all_off_btn.grid(row=3, column=2, columnspan=2, pady=10, sticky="ew", padx=2)
    ui_controls_to_toggle.append(all_off_btn)
    for i in range(4): output_controls_frame.columnconfigure(i, weight=1)

    other_controls_frame = ttk.LabelFrame(right_panel, text="Device Controls & Info", padding=10)
    other_controls_frame.pack(pady=5, fill="x")
    btn_other_controls_data: List[Tuple[str, Callable[[], Any]]] = [
        ("Restart Controller", _safe_restart_controller_cmd),
        ("Enable Input Reports", cmd_enable_input_reports),
        ("Disable Input Reports", cmd_disable_input_reports),
        ("Parsed Controller Info", display_parsed_controller_info),
        ("Get HID Report Desc.", get_and_display_hid_report_descriptor),
        ("Dump F1 Banks (A+B)", dump_f1_banks_gui),
        ("Dump → File…", dump_f1_banks_to_file),
        ("Flash from File…", flash_f1_banks_from_file),
    ]
    max_btns_per_row_other = 2
    for i, (text, cmd) in enumerate(btn_other_controls_data):
        row_idx, col_idx = divmod(i, max_btns_per_row_other)
        b = ttk.Button(other_controls_frame, text=text, command=cmd)
        b.grid(row=row_idx, column=col_idx, sticky="ew", padx=3, pady=3)
        ui_controls_to_toggle.append(b)

    next_btn_row, next_btn_col = divmod(len(btn_other_controls_data), max_btns_per_row_other)
    
    data_log_toggle_button = ttk.Button(other_controls_frame,
                                        text="Disable Input Log" if data_logging_enabled else "Enable Input Log",
                                        command=toggle_data_logging)
    data_log_toggle_button.grid(row=next_btn_row, column=next_btn_col, sticky="ew", padx=3, pady=3)
    ui_controls_to_toggle.append(data_log_toggle_button)

    for i in range(max_btns_per_row_other): other_controls_frame.columnconfigure(i, weight=1)

    bt_pair_frame = ttk.LabelFrame(right_panel, text="Bluetooth Pairing", padding=10)
    bt_pair_frame.pack(pady=5, fill="x")

    ttk.Label(bt_pair_frame, text="Current Paired MAC:").grid(row=0, column=0, sticky="w", padx=(2,5), pady=(10,5))

    current_paired_mac_var = tk.StringVar()
    current_paired_mac_var.set("N/A (Connect Controller)")

    current_paired_mac_display = ttk.Entry(bt_pair_frame, textvariable=current_paired_mac_var, width=20, state='readonly')
    current_paired_mac_display.grid(row=0, column=1, columnspan=2, sticky="ew", padx=5, pady=(10,5))
    ui_controls_to_toggle.append(current_paired_mac_display)

    ttk.Label(bt_pair_frame, text="Host BT MAC:").grid(row=1, column=0, sticky="w", padx=(2,5), pady=5)

    host_mac_entry_var = tk.StringVar()
    host_mac_entry_var.set(get_host_bt_mac_address())

    host_mac_entry_widget = ttk.Entry(bt_pair_frame, textvariable=host_mac_entry_var, width=20)
    host_mac_entry_widget.grid(row=1, column=1, sticky="ew", padx=5, pady=5)
    ui_controls_to_toggle.append(host_mac_entry_widget)

    pair_button = ttk.Button(bt_pair_frame, text="Pair MAC", command=send_bt_pair_request_cmd)
    pair_button.grid(row=1, column=2, sticky="ew", padx=(5,2), pady=5)
    ui_controls_to_toggle.append(pair_button)

    bt_pair_frame.columnconfigure(1, weight=1)

    custom_report_frame = ttk.LabelFrame(right_panel, text="Manual Custom Feature Report Interaction", padding=10)
    custom_report_frame.pack(pady=5, fill="x")

    ttk.Label(custom_report_frame, text="SET ID (hex/dec):").grid(row=0, column=0, sticky="w", padx=2, pady=2)
    set_id_entry = ttk.Entry(custom_report_frame, width=10)
    set_id_entry.grid(row=0, column=1, sticky="ew", padx=2)
    set_id_entry.insert(0, "0xF4")
    ui_controls_to_toggle.append(set_id_entry)

    set_button = ttk.Button(custom_report_frame, text="Execute SET",
                            command=lambda: send_custom_feature_report_data_gui(set_id_entry.get(), set_payload_entry.get()))
    set_button.grid(row=0, column=2, rowspan=2, sticky="nsew", padx=5, pady=2)
    ui_controls_to_toggle.append(set_button)

    ttk.Label(custom_report_frame, text="Payload (hex bytes):").grid(row=1, column=0, sticky="w", padx=2, pady=2)
    set_payload_entry = ttk.Entry(custom_report_frame)
    set_payload_entry.grid(row=1, column=1, sticky="ew", padx=2)
    set_payload_entry.insert(0, "42 02")
    ui_controls_to_toggle.append(set_payload_entry)

    ttk.Label(custom_report_frame, text="GET ID (hex/dec):").grid(row=2, column=0, sticky="w", padx=2, pady=(10,2))
    get_id_entry = ttk.Entry(custom_report_frame, width=10)
    get_id_entry.grid(row=2, column=1, sticky="ew", padx=2, pady=(10,2))
    get_id_entry.insert(0, "0xF2")
    ui_controls_to_toggle.append(get_id_entry)

    get_button = ttk.Button(custom_report_frame, text="Execute GET",
                            command=lambda: get_custom_feature_report_data_gui(get_id_entry.get()))
    get_button.grid(row=2, column=2, sticky="ew", padx=5, pady=(10,2))
    ui_controls_to_toggle.append(get_button)

    custom_report_frame.columnconfigure(1, weight=1)

    feature_report_display_frame = ttk.LabelFrame(root, text="Output Log / Descriptor Data", padding=10)
    feature_report_display_frame.pack(side="bottom", fill="both", expand=True, padx=5, pady=(0,5))
    feature_text_area = scrolledtext.ScrolledText(feature_report_display_frame, height=15, width=100, wrap=tk.WORD,
                                                  font=("Consolas", 9),
                                                  bg=TEXT_AREA_BG, fg=TEXT_AREA_FG,
                                                  insertbackground=FG_COLOR,
                                                  relief=tk.FLAT,
                                                  borderwidth=0,
                                                  highlightthickness=1,
                                                  highlightcolor=ACCENT_COLOR,
                                                  highlightbackground=BORDER_COLOR)
    feature_text_area.pack(fill="both", expand=True)
    feature_text_area.configure(state='disabled')

    enable_controller_ui(False)
    threading.Thread(target=usb_polling_loop, daemon=True).start()
    if root:
        root.after(10, update_loop)
        root.mainloop()
    else:
        logger.critical("Failed to initialize Tkinter root window. Application cannot start.")

def main_gui_app_cleanup() -> None:
    global global_dev_handle, global_intf_num, input_reports_enabled

    logger.info("Cleaning up application resources...")
    dev = global_dev_handle
    if dev is not None and global_intf_num is not None:
        try:
            if input_reports_enabled:
                try:
                    logger.info("Attempting to send disable input reports command on exit...")
                    wValue = WVALUE_HIGH_FEATURE | 0xF4
                    dev.ctrl_transfer(
                        BM_REQUEST_TYPE_SET_FEATURE_HID_CLASS, BREQUEST_SET_REPORT,
                        wValue, global_intf_num, DISABLE_INPUT_STREAMING_PAYLOAD, timeout=500
                    )
                    logger.info("Disable input reports command sent successfully.")
                except Exception as e_disable:
                    logger.warning(f"Failed to send disable input reports command on cleanup: {e_disable}")

            logger.info(f"Releasing interface {global_intf_num}...")
            usb.util.release_interface(dev, global_intf_num)
            logger.info(f"Interface {global_intf_num} released.")

            logger.info("Disposing USB device resources...")
            usb.util.dispose_resources(dev)
            logger.info("USB resources disposed.")

        except Exception as e_cleanup:
            logger.warning(f"Exception during USB cleanup: {e_cleanup}", exc_info=True)
        finally:
            global_dev_handle = None
            global_intf_num = None
            endpoint_desc = None
            input_reports_enabled = False
    else:
        logger.info("No active device handle to clean up.")

    logger.info("Script finished.")

def update_loop() -> None:
    global current_polling_rate_hz, input_reports_enabled, ps_button_warning_shown
    global last_reconnect_attempt_time, global_dev_handle
    global global_calibration_data
    global data_logging_enabled

    if not (root and root.winfo_exists()):
        logger.info("Root window closed or not available, stopping UI update_loop.")
        return

    current_time_ms = time.monotonic() * 1000

    if global_dev_handle is None:
        enable_controller_ui(False)
        if connection_status_label:
            current_status_text = connection_status_label.cget("text")
            if not any(s in current_status_text for s in ["Connecting", "Error", "Not Found"]):
                connection_status_label.config(text="Disconnected")

        if current_paired_mac_var and "N/A (" not in current_paired_mac_var.get():
            current_paired_mac_var.set("N/A (Disconnected)")

        if (current_time_ms - last_reconnect_attempt_time > RECONNECT_INTERVAL_MS):
            last_reconnect_attempt_time = current_time_ms
            logger.info(f"Update loop: Attempting to reconnect controller (Last attempt: {last_reconnect_attempt_time:.0f}ms)")
            threading.Thread(target=initialize_and_update_ui, daemon=True).start()
    else:
        enable_controller_ui(True)
        if connection_status_label and "Connected to DS3" not in connection_status_label.cget("text"):
             connection_status_label.config(text="Connected to DS3")

    if polling_rate_label: polling_rate_label.config(text=f"Polling Rate: {current_polling_rate_hz:.2f} Hz")

    data: Optional[List[int]] = latest_input_data.get("data")

    if data_logging_enabled:
        logger.warning(f"Update loop: Data received: {data}")

    if data and global_dev_handle:
        ps_button_warning_shown = False
        if data[0] == REPORT_ID_INPUT_ACTUAL:
            if len(data) >= INPUT_REPORT_MIN_LENGTH_STICKS:
                lx, ly, rx, ry = (data[INPUT_REPORT_STICK_LX_OFFSET], data[INPUT_REPORT_STICK_LY_OFFSET],
                                  data[INPUT_REPORT_STICK_RX_OFFSET], data[INPUT_REPORT_STICK_RY_OFFSET])
                norm = lambda val: (val - 128.0) / 128.0
                scale_factor = center * 0.90

                if canvas_left and canvas_left.winfo_exists() and dot_left and center > 0:
                    new_lx_coord = center + norm(lx) * scale_factor
                    new_ly_coord = center + norm(ly) * scale_factor
                    canvas_left.coords(dot_left, new_lx_coord - radius, new_ly_coord - radius, new_lx_coord + radius, new_ly_coord + radius)

                if canvas_right and canvas_right.winfo_exists() and dot_right and center > 0:
                    new_rx_coord = center + norm(rx) * scale_factor
                    new_ry_coord = center + norm(ry) * scale_factor
                    canvas_right.coords(dot_right, new_rx_coord - radius, new_ry_coord - radius, new_rx_coord + radius, new_ry_coord + radius)

            if len(data) > INPUT_REPORT_BUTTONS_BYTE_OFFSET_1:
                b0 = data[INPUT_REPORT_BUTTONS_BYTE_OFFSET_0]
                b1 = data[INPUT_REPORT_BUTTONS_BYTE_OFFSET_1]
                for var, mask, is_low_byte_btn in button_vars:
                    if var: var.set(1 if ((b0 if is_low_byte_btn else b1) & mask) else 0)

            if ps_var and len(data) >= INPUT_REPORT_MIN_LENGTH_PS_BUTTON:
                ps_var.set(1 if (data[INPUT_REPORT_PS_BUTTON_BYTE_OFFSET] & 0x01) else 0)

            for name, byte_idx in pressure_map.items():
                if name in pressure_vars and pressure_vars[name]:
                    if len(data) > byte_idx:
                        pressure_vars[name].set(data[byte_idx])
                    else:
                        pressure_vars[name].set(0)

            if len(data) >= INPUT_REPORT_MIN_LENGTH_SENSORS:
                raw_ax_script = s16_le(data, INPUT_REPORT_ACCEL_X_LOW_BYTE_OFFSET)
                raw_ay_script = s16_le(data, INPUT_REPORT_ACCEL_Y_LOW_BYTE_OFFSET)
                raw_az_script = s16_le(data, INPUT_REPORT_ACCEL_Z_LOW_BYTE_OFFSET)
                raw_gz_script = s16_le(data, INPUT_REPORT_GYRO_Z_LOW_BYTE_OFFSET)

                biased_ax_script = raw_ax_script - global_calibration_data["acc_x_bias"]
                biased_ay_script = raw_ay_script - global_calibration_data["acc_y_bias"]
                biased_az_script = raw_az_script - global_calibration_data["acc_z_bias"]

                script_accel_ms2 = {
                    "x": (biased_ax_script / ACCEL_SENSITIVITY_LSB_PER_G) * GRAVITY_MS2,
                    "y": (biased_ay_script / ACCEL_SENSITIVITY_LSB_PER_G) * GRAVITY_MS2,
                    "z": (biased_az_script / ACCEL_SENSITIVITY_LSB_PER_G) * GRAVITY_MS2,
                }

                centered_offset_gz_script = (raw_gz_script - 512) - global_calibration_data["gyro_z_offset"]
                script_gyro_dps = {
                    "pitch": 0.0,
                    "roll":  0.0,
                    "yaw":   centered_offset_gz_script / GYRO_SENSITIVITY_LSB_PER_DPS
                }

                final_display_ax_ms2 = script_accel_ms2["x"]
                final_display_ay_ms2 = script_accel_ms2["z"]
                final_display_az_ms2 = script_accel_ms2["y"]

                if sensor_vars.get("pitch_dps"): sensor_vars["pitch_dps"].set(f"{script_gyro_dps['pitch']:+.1f}°/S")
                if sensor_vars.get("yaw_dps"):   sensor_vars["yaw_dps"].set(f"{script_gyro_dps['yaw']:+.1f}°/S")
                if sensor_vars.get("roll_dps"):  sensor_vars["roll_dps"].set(f"{script_gyro_dps['roll']:+.1f}°/S")

                if sensor_vars.get("display_ax_ms2"): sensor_vars["display_ax_ms2"].set(f"{final_display_ax_ms2:+.4f} m/s²")
                if sensor_vars.get("display_ay_ms2"): sensor_vars["display_ay_ms2"].set(f"{final_display_ay_ms2:+.4f} m/s²")
                if sensor_vars.get("display_az_ms2"): sensor_vars["display_az_ms2"].set(f"{final_display_az_ms2:+.4f} m/s²")

            else:
                for _, key, _ in GYRO_AXES:
                    if sensor_vars.get(key): sensor_vars[key].set("N/A")
                for _, key, _ in ACCEL_AXES:
                    if sensor_vars.get(key): sensor_vars[key].set("N/A")

        else:
            logger.debug(f"Received unexpected report ID {data[0]:02X}. Expected {REPORT_ID_INPUT_ACTUAL:02X}. Clearing relevant UI.")
            clear_input_ui_elements()
            for key in sensor_vars:
                 if sensor_vars[key]: sensor_vars[key].set("N/A (OthRpt)")
    else:
        if global_dev_handle and input_reports_enabled and current_polling_rate_hz == 0.0 and not ps_button_warning_shown:
            logger.info("Input reports are enabled, but no data stream (polling rate is 0 Hz). Controller might require PS button press to start streaming.")
            ps_button_warning_shown = True
        elif not global_dev_handle or not input_reports_enabled:
            ps_button_warning_shown = False

        clear_input_ui_elements()

    if root and root.winfo_exists():
        root.after(15, update_loop)

def initialize_and_update_ui() -> None:
    is_initialized = initialize_controller()
    if root and root.winfo_exists():
        root.after(0, lambda: enable_controller_ui(is_initialized))
        pass

def clear_input_ui_elements() -> None:

    if canvas_left and hasattr(canvas_left, 'winfo_exists') and canvas_left.winfo_exists() and dot_left and center > 0:
        try:
            canvas_left.coords(dot_left, center - radius, center - radius, center + radius, center + radius)
        except tk.TclError:
            pass
    if canvas_right and hasattr(canvas_right, 'winfo_exists') and canvas_right.winfo_exists() and dot_right and center > 0:
        try:
            canvas_right.coords(dot_right, center - radius, center - radius, center + radius, center + radius)
        except tk.TclError:
            pass

    for var, _, _ in button_vars:
        if var:
            try:
                var.set(0)
            except tk.TclError:
                pass
    if ps_var:
        try:
            ps_var.set(0)
        except tk.TclError:
            pass

    for name_key in pressure_map:
        if name_key in pressure_vars and pressure_vars[name_key]:
            try:
                pressure_vars[name_key].set(0)
            except tk.TclError:
                pass
            
    for _, key, _ in GYRO_AXES:
        if sensor_vars.get(key):
            try:
                sensor_vars[key].set("N/A")
            except tk.TclError:
                pass
                
    for _, key, _ in ACCEL_AXES:
        if sensor_vars.get(key):
            try:
                sensor_vars[key].set("N/A")
            except tk.TclError:
                pass

    global global_dev_handle
    if global_dev_handle is None:
        global stick_calib_display_vars
        for var in stick_calib_display_vars:
            if var: var.set("N/A")
        
        global stick_calib_axis_value_vars
        for var in stick_calib_axis_value_vars:
            if var: var.set("----")

if __name__ == '__main__':
    try:
        main_gui_app()
    except SystemExit:
        logger.info("Application is exiting via SystemExit.")
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user (Ctrl+C). Exiting.")
    except Exception as e_main:
        logger.critical(f"Unhandled top-level exception in __main__: {e_main}", exc_info=True)
        try:
            if 'root' in globals() and root and root.winfo_exists():
                messagebox.showerror("Fatal Error", f"A critical error occurred:\n{e_main}\n\nThe application will now exit.", parent=root)
                root.destroy()
            else:
                tk_error_root = tk.Tk()
                tk_error_root.withdraw()
                messagebox.showerror("Fatal Error", f"A critical error occurred during startup or shutdown:\n{e_main}\n\nThe application will now exit.", parent=None)
                tk_error_root.destroy()
        except Exception as e_msgbox:
            logger.error(f"Could not display graphical error message: {e_msgbox}")
    finally:
        main_gui_app_cleanup()
