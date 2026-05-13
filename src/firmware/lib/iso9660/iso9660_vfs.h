#ifndef OF_ISO9660_VFS_H
#define OF_ISO9660_VFS_H

#include <stddef.h>
#include <stdint.h>

#ifndef L9660_SINGLEBUFFER
#define L9660_SINGLEBUFFER 1
#endif
#include "lib9660.h"

typedef int (*iso9660_slot_read_fn)(uint32_t slot_id, uint32_t offset,
                                    void *dst, uint32_t len);

typedef struct {
    /* Must stay first: lib9660 passes l9660_fs* back to read_sector. */
    l9660_fs fs;
    uint32_t slot_id;
    iso9660_slot_read_fn read;
    int mounted;
} iso9660_mount_t;

typedef struct {
    l9660_file file;
} iso9660_file_t;

typedef struct {
    l9660_dir dir;
} iso9660_dir_t;

typedef struct {
    char name[256];
    uint32_t size;
    int is_dir;
} iso9660_dirent_t;

int iso9660_mount(iso9660_mount_t *mount, uint32_t slot_id,
                  iso9660_slot_read_fn read);
void iso9660_unmount(iso9660_mount_t *mount);

int iso9660_open_file(iso9660_mount_t *mount, const char *path,
                      iso9660_file_t *out);
int iso9660_open_dir(iso9660_mount_t *mount, const char *path,
                     iso9660_dir_t *out);
int iso9660_read_file(iso9660_mount_t *mount, iso9660_file_t *file,
                      void *dst, uint32_t len);
int iso9660_seek_file(iso9660_file_t *file, int64_t offset, int whence);
uint32_t iso9660_tell_file(const iso9660_file_t *file);
uint32_t iso9660_size_file(const iso9660_file_t *file);

int iso9660_read_dir(iso9660_dir_t *dir, iso9660_dirent_t *out);
int iso9660_stat(iso9660_mount_t *mount, const char *path,
                 uint32_t *size_out, int *is_dir_out);

#endif /* OF_ISO9660_VFS_H */
