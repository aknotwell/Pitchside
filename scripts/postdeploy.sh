#!/bin/sh
# After `azd deploy`: point the simulator job at the image just deployed to the interpreter
# (one image for every Python service), so a new match always runs the current code.
set -eu
RG="${AZURE_RESOURCE_GROUP}"
IMAGE=$(az containerapp show -g "$RG" -n "$INTERPRETER_APP_NAME" --query "properties.template.containers[0].image" -o tsv)
case "$IMAGE" in
  *quickstart*) echo "interpreter still on placeholder image; skipping job update"; exit 0 ;;
esac
echo "simulator job $SIM_JOB_NAME -> $IMAGE"
az containerapp job update -g "$RG" -n "$SIM_JOB_NAME" --image "$IMAGE" -o none
