#!/system/bin/sh
set -eu
cd "$1"
assets="$2"
display_mode="${3:-keep-display}"
quality_mode="${4:-pixel_exact}"
mkdir -p evidence/timings
getprop ro.soc.model > evidence/soc.txt

render() {
    scene="$1"
    variant="$2"
    folder="$3"
    case "$variant" in
        candidate) set -- ./candidate.filamat ;;
        native) set -- --native-reference ;;
        stock) set -- ;;
    esac
    if [ "$display_mode" = wake-screen ]; then
        input keyevent KEYCODE_WAKEUP
    fi
    "$assets/runner" "$assets/$scene.glb" "$assets/environment.ktx" "$folder" "$scene" "$@"
}

record() {
    log="$1"
    shift
    if ! render "$@" > "$log" 2>&1; then
        tail -30 "$log" >&2
        exit 1
    fi
}

for scene in DamagedHelmet Fox; do
    for round in 1 2 3 4; do
        if [ "$((round % 2))" -eq 1 ]; then
            set -- stock candidate
        else
            set -- candidate stock
        fi
        for variant in "$@"; do
            LLM4AD_FSR_TIMINGS_ONLY=1 record "evidence/timings/$scene-$round-$variant.txt" "$scene" "$variant" .
        done
    done
done

mkdir -p evidence/quality evidence/previews
for scene in DamagedHelmet Fox; do
    for variant in stock candidate; do
        mkdir -p "captures/$scene/$variant"
        record "evidence/timings/$scene-capture-$variant.txt" "$scene" "$variant" "captures/$scene/$variant"
    done
    if [ "$quality_mode" = flip ]; then
        mkdir -p "captures/$scene/native" "evidence/flip/$scene"
        record "evidence/timings/$scene-capture-native.txt" "$scene" native "captures/$scene/native"
        grep -qx 'MODE native' "evidence/timings/$scene-capture-native.txt"
        cp "captures/$scene/native/frame-060.png" "evidence/previews/$scene-native.png"
    fi
    mkdir "evidence/quality/$scene"
    for reference in captures/"$scene"/stock/frame-*.png; do
        frame="${reference##*/}"
        "$assets/runner" --diff "$reference" "captures/$scene/candidate/$frame" \
            > "evidence/quality/$scene/${frame%.png}.json"
        if [ "$quality_mode" = flip ]; then
            set --
            if [ "$frame" = frame-060.png ]; then
                set -- "evidence/previews/$scene"
            fi
            OMP_NUM_THREADS=4 "$assets/runner" --flip "captures/$scene/native/$frame" "$reference" \
                "captures/$scene/candidate/$frame" "$@" > "evidence/flip/$scene/${frame%.png}.json"
        fi
    done
    for variant in stock candidate; do
        cp "captures/$scene/$variant/frame-060.png" "evidence/previews/$scene-$variant.png"
    done
done
tar -czf evidence.tgz evidence
