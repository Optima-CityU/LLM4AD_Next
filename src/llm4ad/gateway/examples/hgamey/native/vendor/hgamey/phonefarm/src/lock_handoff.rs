//! Local fd handoff between GOL and the selected phonefarm. No lock implementation here.
use std::fs::File;
use std::io::{self, Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, RawFd};
use std::os::unix::net::UnixStream;

pub fn send(stream: &mut UnixStream, fds: &[RawFd], payload: &[u8]) -> io::Result<()> {
    let bytes = std::mem::size_of_val(fds);
    let mut control =
        vec![
            0usize;
            unsafe { libc::CMSG_SPACE(bytes as _) } as usize / std::mem::size_of::<usize>() + 1
        ];
    let mut byte = [0u8];
    let mut iov = libc::iovec {
        iov_base: byte.as_mut_ptr().cast(),
        iov_len: 1,
    };
    let mut message: libc::msghdr = unsafe { std::mem::zeroed() };
    message.msg_iov = &mut iov;
    message.msg_iovlen = 1;
    message.msg_control = control.as_mut_ptr().cast();
    message.msg_controllen = unsafe { libc::CMSG_SPACE(bytes as _) } as _;
    unsafe {
        let header = libc::CMSG_FIRSTHDR(&message);
        (*header).cmsg_level = libc::SOL_SOCKET;
        (*header).cmsg_type = libc::SCM_RIGHTS;
        (*header).cmsg_len = libc::CMSG_LEN(bytes as _) as _;
        std::ptr::copy_nonoverlapping(fds.as_ptr().cast::<u8>(), libc::CMSG_DATA(header), bytes);
        if libc::sendmsg(stream.as_raw_fd(), &message, 0) < 0 {
            return Err(io::Error::last_os_error());
        }
    }
    stream.write_all(&(payload.len() as u32).to_be_bytes())?;
    stream.write_all(payload)
}

pub fn receive(stream: &mut UnixStream) -> io::Result<(Vec<File>, Vec<u8>)> {
    let mut control = [0usize; 16];
    let mut byte = [0u8];
    let mut iov = libc::iovec {
        iov_base: byte.as_mut_ptr().cast(),
        iov_len: 1,
    };
    let mut message: libc::msghdr = unsafe { std::mem::zeroed() };
    message.msg_iov = &mut iov;
    message.msg_iovlen = 1;
    message.msg_control = control.as_mut_ptr().cast();
    message.msg_controllen = std::mem::size_of_val(&control) as _;
    let count = unsafe { libc::recvmsg(stream.as_raw_fd(), &mut message, 0) };
    if count < 0 {
        return Err(io::Error::last_os_error());
    }
    if count == 0 {
        return Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "phonefarm exited before lock handoff",
        ));
    }
    let mut files = Vec::new();
    unsafe {
        let header = libc::CMSG_FIRSTHDR(&message);
        if !header.is_null() {
            let count = ((*header).cmsg_len as usize - libc::CMSG_LEN(0) as usize)
                / std::mem::size_of::<RawFd>();
            for fd in std::slice::from_raw_parts(libc::CMSG_DATA(header).cast::<RawFd>(), count) {
                files.push(File::from_raw_fd(*fd));
            }
        }
    }
    // Only the first byte waits for acquisition; the local helper now sends the complete scope.
    stream.set_read_timeout(None)?;
    let mut length = [0; 4];
    stream.read_exact(&mut length)?;
    let mut payload = vec![0; u32::from_be_bytes(length) as usize];
    stream.read_exact(&mut payload)?;
    Ok((files, payload))
}
