#!/bin/bash
# Wrapper: submit llm_call.py as an sbatch job.
# All arguments are passed through to llm_call.py.
# Output goes to <skill_dir>/sbatch_output/%j-%x.md
#
# Usage:
#   bash llm_call.sh "your prompt"
#   bash llm_call.sh --preset summarize < input.txt
#   echo "text" | bash llm_call.sh --preset judge

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="${SKILL_DIR}/sbatch_output"
mkdir -p "${OUTPUT_DIR}"

# Capture stdin (if any) to a temp file — sbatch doesn't forward stdin
STDIN_FILE=""
if ! [[ -t 0 ]]; then
  STDIN_FILE="${OUTPUT_DIR}/.stdin.$$"
  cat > "${STDIN_FILE}"
fi

# Build a safely quoted argument list, resolving relative --image paths
ARGS=()
for ((i=1; i<=$#; i++)); do
  arg="${@:$i:1}"
  if [[ "${arg}" == "--image" ]]; then
    ARGS+=("$(printf '%q' "${arg}")")
    i=$((i + 1))
    path="${@:$i:1}"
    # If relative (not starting with /), resolve to absolute
    if [[ -n "${path}" && "${path}" != /* ]]; then
      path="${PWD}/${path}"
    fi
    ARGS+=("$(printf '%q' "${path}")")
    continue
  fi
  ARGS+=("$(printf '%q' "${arg}")")
done

# Generate a short job name from the first non-flag argument
JOB_NAME="llm-call"
for arg in "$@"; do
  # Skip flags (--xxx or -x) and their values
  if [[ "${arg}" =~ ^--?[a-zA-Z] ]]; then
    continue
  fi
  SANE="$(printf '%s' "${arg}" | tr -dc 'a-zA-Z0-9_-' | head -c 20)"
  if [[ -n "${SANE}" ]]; then
    JOB_NAME="lc-${SANE}"
    break
  fi
done

# Write the sbatch submission script
SBATCH_SCRIPT=$(mktemp "${OUTPUT_DIR}/.sbatch_script.XXXXXX")

cleanup() {
  # Only remove the local sbatch script. STDIN_FILE must survive until the
  # compute job reads it (the job deletes it after use). On submit failure,
  # clean stdin below so we do not leak temp files.
  rm -f "${SBATCH_SCRIPT}"
}
trap cleanup EXIT

cat > "${SBATCH_SCRIPT}" <<'SBATCH_HEADER'
#!/bin/bash
#SBATCH --partition=general
#SBATCH --qos=advanced
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=2G
#SBATCH --time=00:15:00
SBATCH_HEADER

cat >> "${SBATCH_SCRIPT}" <<EOF
#SBATCH --job-name=${JOB_NAME}
#SBATCH --output=${OUTPUT_DIR}/%j-%x.md
#SBATCH --error=${OUTPUT_DIR}/%j-%x.md

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy

cd "${SKILL_DIR}"
EOF

# Append the python invocation, with optional stdin redirect
if [[ -n "${STDIN_FILE}" ]]; then
  cat >> "${SBATCH_SCRIPT}" <<EOF
python llm_call.py ${ARGS[@]} < "${STDIN_FILE}"
rm -f "${STDIN_FILE}"
EOF
else
  cat >> "${SBATCH_SCRIPT}" <<EOF
python llm_call.py ${ARGS[@]}
EOF
fi

# Submit
if ! SBATCH_OUTPUT="$(sbatch "${SBATCH_SCRIPT}")"; then
  rm -f "${STDIN_FILE}"
  echo "sbatch failed" >&2
  exit 1
fi
echo "${SBATCH_OUTPUT}"

JOB_ID="$(grep -oP '\d+' <<< "${SBATCH_OUTPUT}")"
OUT_FILE="${OUTPUT_DIR}/${JOB_ID}-${JOB_NAME}.md"
echo "Output: ${OUT_FILE}"
echo "Monitor: tail -f ${OUT_FILE}"
