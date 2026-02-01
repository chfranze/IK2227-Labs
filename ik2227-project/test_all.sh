for f in tests/*.sh; do
  echo "Running $f"
  bash "$f"
done
