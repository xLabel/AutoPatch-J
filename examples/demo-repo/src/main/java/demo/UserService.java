package demo;

public class UserService {
    public boolean isAdmin(User user) {
        return user.getName().equals("admin");
    }
}
