# Project Instructions

## Testing Workflow

After making changes to the FPGA design or firmware:

1. **Deploy the FPGA bitstream:**
   ```bash
   cd src/fpga && make program
   ```

2. **Check the output using the capture tool:**
   ```bash
   tools/capture_ocr.sh
   ```
