import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import Composer from "./content/Composer";
import PostDetail from "./content/PostDetail";
import PostList from "./content/PostList";

export default function Posts() {
  const { can } = useAuth();
  return (
    <Routes>
      <Route index element={<PostList />} />
      <Route path="new" element={can("content.create") ? <Composer /> : <Navigate to="/posts" replace />} />
      <Route path=":id" element={<PostDetail />} />
    </Routes>
  );
}
