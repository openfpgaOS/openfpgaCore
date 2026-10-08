// Extract setup results from the original RTL. The extracted stream is an
// oracle for validating the new interface, NOT a simulated new accelerator.
static FILE *converted;
static std::vector<uint32_t> plane_commands;
static bool tap_copy;
static unsigned tap_remaining;
static uint64_t plane_count;

static void tap_before() {
    if (!converted || !tb->reset_n) return;
    if (tb->tap_header_valid) {
        if (tap_remaining) throw std::runtime_error("Unfinished tapped command");
        uint32_t h=tb->tap_header, op=h>>24;
        // Matrix/cache uploads are private to the original frontend. Both
        // raw and indexed draws become complete, ordered plane commands.
        tap_copy=!(op==0x4b || op==0x4e || op==0x4f || (op>=0x50 && op<=0x5f));
        tap_remaining=h&8191;
        if (tap_copy) plane_commands.push_back(h);
    }
    if (tb->tap_payload_valid) {
        if (!tap_remaining) throw std::runtime_error("Unexpected tapped payload");
        --tap_remaining;
        if (tap_copy) plane_commands.push_back(tb->tap_payload);
    }
}

static void tap_after() {
    if (!converted) return;
    if (!tb->tap_rgb) throw std::runtime_error("Reference needs a non-RGB plane format");
    unsigned n=tb->tap_plane_words;
    plane_commands.push_back(0x60000000u|n);
    for (unsigned i=0;i<n;i++) plane_commands.push_back(tb->tap_planes[i]);
    plane_count++;
}

static void tap_flush() {
    if (!converted) return;
    if (tap_remaining) throw std::runtime_error("Tapped batch ended inside a command");
    size_t start=0,end=0;
    while (end<plane_commands.size()) {
        size_t n=1+(plane_commands[end]&8191u);
        if (n>4095 || n>plane_commands.size()-end) throw std::runtime_error("Invalid tapped command");
        if (end+n-start>4095) {
            uint32_t h[2]={GV_TAG_CMDS,uint32_t(end-start)};
            fwrite(h,4,2,converted);fwrite(plane_commands.data()+start,4,end-start,converted);
            start=end;
        }
        end+=n;
    }
    if (end>start) {
        uint32_t h[2]={GV_TAG_CMDS,uint32_t(end-start)};
        fwrite(h,4,2,converted);fwrite(plane_commands.data()+start,4,end-start,converted);
    }
    plane_commands.clear();
}
