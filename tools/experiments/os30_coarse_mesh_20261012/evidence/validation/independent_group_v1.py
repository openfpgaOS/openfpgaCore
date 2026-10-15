"""Independent source/call expansion for AJ03; no runtime code is imported.

The physical descriptor snapshot is taken at accepted GROUP, before member
fetch. A second handshake witness must later prove the fetched words agree.
"""
import bisect
import struct

CALL_IDENTITY = ("phase", "a0", "a1", "a2", "s1", "ra")


def expand(uploaded, calls, baseline_calls, descriptor_groups, phase_at,
           immutable, pair, dead):
    """Return logical jobs/groups and exact original-to-real CPU call mapping.

    descriptor_groups: first sequence -> {cycle, pointer, count, words}.
    Jobs use the original baseline call index, so raw START reordering or
    intentionally omitted CPU callbacks never weaken logical ownership.
    """
    jobs, groups, plain, expected_ctx = [], [], [], []
    context = None
    skipped = set()
    claimed = set()
    upload_requests = []

    def add_job(fields, common_context, upload, group=None, member=0):
        seq, source, vtx, cmdsrc, config, flags, u, v, cpusrc, cpucmd, alpha = fields
        assert seq == len(jobs) + 1
        assert config >> 10 == 0 and not (config & 128) and 0 < (config & 127) <= 64
        assert flags >> 19 == 0 and alpha <= 255 and common_context[23] in (0, 1)
        if flags & (1 << 16):
            assert u <= 524280 and v <= 524280 and not ((u | v) & 7)
        count = (vtx >> 12) & 255
        assert 0 < count <= 32 and ((vtx >> 1) & 127) == count and vtx >> 24 == 1
        phase = phase_at(upload["end"])
        matches = [i for i, c in enumerate(baseline_calls) if c["phase"] == phase and
                   (c["a0"], c["a1"], c["a2"], c["s1"] + 8) ==
                   (count, 0, cpusrc, cpucmd) and i not in claimed]
        # Repeated immutable lists are matched in original dynamic call order.
        assert matches, ("unowned logical LOAD", seq)
        callidx = matches[0]
        if group and member:
            assert callidx == group["calls"][0] + member, "GROUP skips a state/list/CPU LOAD boundary"
            assert jobs[-1]["next_command"] == cpucmd - 8, "GROUP is not immutable straight-line adjacency"
            skipped.add(callidx)
        assert not jobs or jobs[-1]["call"] < callidx, "logical LOAD submission order changed"
        assert pair(cpucmd - 8) == (vtx, cpusrc), "original immutable VTX source changed"
        raw = immutable(cpusrc, count * 24)
        commands = immutable(cpucmd, (config & 127) * 8)
        assert raw is not None and commands is not None
        assert source % 4 == 0 and source + len(raw) <= 0x1000000
        assert cmdsrc % 8 == 0 and cmdsrc + len(commands) <= 0x1000000
        triangles = 0
        for a, b in struct.iter_unpack("<II", commands):
            assert a >> 24 in (5, 6)
            for w in ([a] if a >> 24 == 5 else [a, b]):
                assert all(((w >> shift) & 255) // 2 < count for shift in (16, 8, 0))
                triangles += 1
        end = cpucmd + (config & 127) * 8
        assert 0 < triangles <= 64 and pair(end)[0] >> 24 not in (5, 6)
        assert dead(end, count), "queued canonical slots escape independently proven suffix"
        job = {"seq": seq, "fields": fields, "context": common_context.copy(),
               "call": callidx, "phase": phase, "upload_end": upload["end"],
               "raw": raw, "commands": commands, "triangles": triangles,
               "next_command": end, "group_first": group["first"] if group else None,
               "member": member}
        jobs.append(job)
        claimed.add(callidx)
        if group:
            group["calls"].append(callidx)

    for upload in uploaded:
        op, d = upload["op"], upload["data"]
        if op == 0x63:
            assert context is None and upload["header"] == 0x6300001d
            assert len(d) == 29 and d[0] == 0
            context = d[1:]
            expected_ctx.extend(enumerate(context))
        elif op in (0x64, 0x65):
            assert context is not None, "JOB/GROUP without indivisible complete context"
            if op == 0x64:
                assert upload["header"] == 0x6400000b and len(d) == 11
                add_job(d, context, upload)
                upload_requests.append({"op": op, "fields": d, "upload_end": upload["end"],
                                        "first": d[0], "count": 1})
            else:
                assert upload["header"] == 0x65000007 and len(d) == 7
                first, pointer, count, flags, u, v, alpha = d
                assert first == len(jobs) + 1 and 0 < count <= 32
                assert first + count - 1 <= 0xffffffff
                assert pointer % 8 == 0 and pointer + 24 * count <= 0x1000000
                physical = descriptor_groups[first]
                assert physical["pointer"] == pointer and physical["count"] == count
                assert physical["cycle"] > upload["end"]
                assert len(physical["words"]) == count * 6
                group = {"first": first, "count": count, "pointer": pointer,
                         "take": physical["cycle"], "upload_end": upload["end"],
                         "fields": d, "context": context.copy(), "calls": [],
                         "descriptor_words": physical["words"].copy()}
                for member in range(count):
                    vertex, vtx, commands, config, cpuvertex, cpucommands = physical["words"][6 * member:6 * member + 6]
                    fields = [first + member, vertex, vtx, commands, config,
                              flags, u, v, cpuvertex, cpucommands, alpha]
                    add_job(fields, context, upload, group, member)
                groups.append(group)
                upload_requests.append({"op": op, "fields": d, "upload_end": upload["end"],
                                        "first": first, "count": count})
            context = None
        else:
            assert context is None, "ordinary command interrupted context/JOB-or-GROUP packet"
            plain.extend([upload["header"], *d])
    assert context is None and jobs
    assert set(descriptor_groups) == {g["first"] for g in groups}
    expected_calls = [c for i, c in enumerate(baseline_calls) if i not in skipped]
    assert [[c[k] for k in CALL_IDENTITY] for c in calls] == [
        [c[k] for k in CALL_IDENTITY] for c in expected_calls], "real CPU calls plus explicit GROUP members do not reproduce original traversal"
    original_to_real = {original: real for real, original in enumerate(
        i for i in range(len(baseline_calls)) if i not in skipped)}
    for job in jobs:
        owner = job["call"] - job["member"]
        real = original_to_real[owner]
        assert calls[real]["cycle"] < job["upload_end"], "GROUP published before owning CPU call"
        job["real_call"] = real
    return {"jobs": jobs, "groups": groups, "plain": plain,
            "expected_context_ingress": expected_ctx, "requests": upload_requests,
            "skipped_calls": sorted(skipped), "original_to_real": original_to_real,
            "real_to_original": {v: k for k, v in original_to_real.items()}}
