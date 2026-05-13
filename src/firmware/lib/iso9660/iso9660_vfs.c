#ifndef L9660_SINGLEBUFFER
#define L9660_SINGLEBUFFER 1
#endif

#include "iso9660_vfs.h"
#include <stdbool.h>
#include <string.h>

#define ISO_EIO          5
#define ISO_ENOENT       2
#define ISO_ENOTDIR      20
#define ISO_EISDIR       21
#define ISO_EINVAL       22
#define ISO_ENAMETOOLONG 36

#define ISO_SECTOR_SIZE 2048u
#define ISO_DENT_ISDIR  (1u << 1)

static uint32_t iso_read32(const l9660_duint32 *v) {
    return (uint32_t)v->le[0]
         | ((uint32_t)v->le[1] << 8)
         | ((uint32_t)v->le[2] << 16)
         | ((uint32_t)v->le[3] << 24);
}

static int iso_status_to_errno(l9660_status st) {
    switch (st) {
    case L9660_OK:       return 0;
    case L9660_EIO:
    case L9660_EBADFS:   return -ISO_EIO;
    case L9660_ENOENT:   return -ISO_ENOENT;
    case L9660_ENOTFILE: return -ISO_EISDIR;
    case L9660_ENOTDIR:  return -ISO_ENOTDIR;
    default:             return -ISO_EIO;
    }
}

static int iso_upper(int c) {
    return (c >= 'a' && c <= 'z') ? c - 32 : c;
}

static int iso_path_normalize(const char *path, char *out, size_t cap,
                              int uppercase) {
    size_t n = 0;

    if (!out || cap == 0)
        return -ISO_EINVAL;

    if (!path)
        path = "";

    while (*path == '/' || *path == '\\')
        path++;

    while (*path) {
        int c = (unsigned char)*path++;
        if (c == '\\')
            c = '/';

        if (c == '/') {
            while (*path == '/' || *path == '\\')
                path++;
            if (!*path)
                break;
            if (n == 0 || out[n - 1] == '/')
                continue;
        } else if (uppercase) {
            c = iso_upper(c);
        }

        if (n + 1 >= cap)
            return -ISO_ENAMETOOLONG;
        out[n++] = (char)c;
    }

    if (n > 0 && out[n - 1] == '/')
        n--;
    out[n] = '\0';
    return 0;
}

static bool iso_read_sector(l9660_fs *fs, void *buf, uint32_t sector) {
    iso9660_mount_t *mount = (iso9660_mount_t *)fs;
    uint32_t off = sector * ISO_SECTOR_SIZE;

    if (!mount->read)
        return false;
    if (sector != off / ISO_SECTOR_SIZE)
        return false;

    return mount->read(mount->slot_id, off, buf, ISO_SECTOR_SIZE) == 0;
}

int iso9660_mount(iso9660_mount_t *mount, uint32_t slot_id,
                  iso9660_slot_read_fn read) {
    if (!mount || !read)
        return -ISO_EINVAL;

    memset(mount, 0, sizeof(*mount));
    mount->slot_id = slot_id;
    mount->read = read;

    int rc = iso_status_to_errno(l9660_openfs(&mount->fs, iso_read_sector));
    if (rc < 0) {
        memset(mount, 0, sizeof(*mount));
        return rc;
    }

    mount->mounted = 1;
    return 0;
}

void iso9660_unmount(iso9660_mount_t *mount) {
    if (mount)
        memset(mount, 0, sizeof(*mount));
}

int iso9660_open_file(iso9660_mount_t *mount, const char *path,
                      iso9660_file_t *out) {
    char iso_path[256];
    l9660_dir root;

    if (!mount || !mount->mounted || !out)
        return -ISO_EINVAL;

    int rc = iso_path_normalize(path, iso_path, sizeof(iso_path),
                                !mount->fs.joliet);
    if (rc < 0)
        return rc;
    if (!iso_path[0])
        return -ISO_EISDIR;

    rc = iso_status_to_errno(l9660_fs_open_root(&root, &mount->fs));
    if (rc < 0)
        return rc;

    rc = iso_status_to_errno(l9660_openat(&out->file, &root, iso_path));
    if (rc < 0)
        memset(out, 0, sizeof(*out));
    return rc;
}

int iso9660_open_dir(iso9660_mount_t *mount, const char *path,
                     iso9660_dir_t *out) {
    char iso_path[256];
    l9660_dir root;

    if (!mount || !mount->mounted || !out)
        return -ISO_EINVAL;

    int rc = iso_path_normalize(path, iso_path, sizeof(iso_path),
                                !mount->fs.joliet);
    if (rc < 0)
        return rc;

    rc = iso_status_to_errno(l9660_fs_open_root(&root, &mount->fs));
    if (rc < 0)
        return rc;

    if (!iso_path[0]) {
        out->dir = root;
        return 0;
    }

    rc = iso_status_to_errno(l9660_opendirat(&out->dir, &root, iso_path));
    if (rc < 0)
        memset(out, 0, sizeof(*out));
    return rc;
}

int iso9660_read_file(iso9660_mount_t *mount, iso9660_file_t *file,
                      void *dst, uint32_t len) {
    if (!mount || !mount->mounted || !file || !dst)
        return -ISO_EINVAL;
    if (len == 0)
        return 0;
    if (file->file.position >= file->file.length)
        return 0;

    uint32_t avail = file->file.length - file->file.position;
    if (len > avail)
        len = avail;

    uint32_t sector_off = file->file.first_sector * ISO_SECTOR_SIZE;
    if (file->file.first_sector != sector_off / ISO_SECTOR_SIZE)
        return -ISO_EIO;

    int rc = mount->read(mount->slot_id, sector_off + file->file.position,
                         dst, len);
    if (rc < 0)
        return rc;

    file->file.position += len;
    return (int)len;
}

int iso9660_seek_file(iso9660_file_t *file, int64_t offset, int whence) {
    int64_t new_pos;

    if (!file)
        return -ISO_EINVAL;

    switch (whence) {
    case 0: new_pos = offset; break;
    case 1: new_pos = (int64_t)file->file.position + offset; break;
    case 2: new_pos = (int64_t)file->file.length + offset; break;
    default:
        return -ISO_EINVAL;
    }

    if (new_pos < 0 || new_pos > 0xFFFFFFFFll)
        return -ISO_EINVAL;

    file->file.position = (uint32_t)new_pos;
    return 0;
}

uint32_t iso9660_tell_file(const iso9660_file_t *file) {
    return file ? file->file.position : 0;
}

uint32_t iso9660_size_file(const iso9660_file_t *file) {
    return file ? file->file.length : 0;
}

int iso9660_read_dir(iso9660_dir_t *dir, iso9660_dirent_t *out) {
    l9660_dirent *dent;

    if (!dir || !out)
        return -ISO_EINVAL;

    for (;;) {
        int rc = iso_status_to_errno(l9660_readdir(&dir->dir, &dent));
        if (rc < 0)
            return rc;
        if (!dent)
            return 0;

        if (dent->name_len == 1 &&
            ((unsigned char)dent->name[0] == 0 ||
             (unsigned char)dent->name[0] == 1))
            continue;

        uint32_t n = (uint32_t)l9660_dirent_name(dir->dir.file.fs, dent,
                                                 out->name,
                                                 sizeof(out->name));
        if (n == 0)
            continue;

        out->size = iso_read32(&dent->size);
        out->is_dir = (dent->flags & ISO_DENT_ISDIR) ? 1 : 0;
        return 1;
    }
}

int iso9660_stat(iso9660_mount_t *mount, const char *path,
                 uint32_t *size_out, int *is_dir_out) {
    iso9660_file_t file;
    iso9660_dir_t dir;

    if (!mount || !mount->mounted)
        return -ISO_EINVAL;

    int rc = iso9660_open_file(mount, path, &file);
    if (rc == 0) {
        if (size_out) *size_out = file.file.length;
        if (is_dir_out) *is_dir_out = 0;
        return 0;
    }

    rc = iso9660_open_dir(mount, path, &dir);
    if (rc == 0) {
        if (size_out) *size_out = 0;
        if (is_dir_out) *is_dir_out = 1;
        return 0;
    }

    return rc;
}
